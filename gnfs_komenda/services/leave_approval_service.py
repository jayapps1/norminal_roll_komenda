"""
Leave Approval & Workflow Service for GNFS.
Implements tiered hierarchical approvals, strict anti-self-approval enforcement,
atomic balance reservation/deduction with select_for_update(), and sync with NorminalRoll.
"""

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import models, transaction
from django.utils import timezone

from gnfs_komenda.models import (
    LeaveRequest,
    LeaveApprovalDecision,
    LeaveEntitlement,
    LeaveType,
    NorminalRoll,
)
from gnfs_komenda.security import get_user_role, get_user_assignment
from gnfs_komenda.services.leave_calculator import calculate_leave_dates
from gnfs_komenda.workflow import record_audit_log, create_notification


def is_self_approval(user, leave_request):
    """
    Check if user is attempting to approve their own leave request.
    Strictly checks user ID, staffID match, and email match.
    """
    if not user or not user.is_authenticated:
        return True
    
    # 1. User who submitted the request
    if leave_request.requested_by_id and leave_request.requested_by_id == user.id:
        return True

    # 2. Match applicant's staffID against user's username
    personnel = leave_request.personnel
    if personnel and personnel.staffID:
        if personnel.staffID.strip().lower() == user.username.strip().lower():
            return True

    # 3. Match email if available
    personnel_email = getattr(personnel, 'email', None)
    if personnel_email and user.email:
        if str(personnel_email).strip().lower() == user.email.strip().lower():
            return True

    return False


def resolve_leave_approver(leave_request):
    """
    Resolve the designated command leadership approver for a leave request based on
    command appointments hierarchy:
    - If applicant is active RFO -> National HQ / National Admin
    - If applicant is active DFO/MFO -> Regional Fire Officer (RFO) of applicant's region
    - Otherwise -> Commanding Officer (DFO/MFO) of applicant's district
    """
    from gnfs_komenda.models import CommandAppointment
    personnel = leave_request.personnel
    if not personnel:
        return "National Headquarters"

    # Check if applicant is active RFO
    is_rfo = CommandAppointment.objects.filter(
        personnel=personnel,
        is_active=True,
        appointment_type__in=['RFO', 'ACTING_RFO']
    ).exists()
    if is_rfo:
        return "National Headquarters / Chief Fire Officer"

    # Check if applicant is active DFO/MFO
    is_co = CommandAppointment.objects.filter(
        personnel=personnel,
        is_active=True,
        appointment_type__in=['DFO', 'MFO', 'METRO_FO', 'ACTING_DFO', 'ACTING_MFO', 'ACTING_METRO_FO']
    ).first()
    if is_co:
        reg = is_co.region or (is_co.district.region if is_co.district else None)
        if reg and reg.current_rfo:
            return f"Regional Fire Officer: {reg.current_rfo.personnel.fullname} ({reg.name})"
        return f"Regional Fire Officer ({reg.name if reg else 'Regional HQ'})"

    # Station personnel -> District CO
    station = personnel.station
    district = station.district if station else None
    if district:
        desig = district.co_designation or "DFO"
        if district.current_commanding_officer:
            return f"{desig}: {district.current_commanding_officer.personnel.fullname} ({district.name})"
        return f"{desig} ({district.name})"

    return "National Headquarters"


def can_user_approve_leave(user, leave_request):
    """
    Determine if a user has sufficient hierarchical jurisdiction to approve a leave request.
    Anti-self-approval is strictly enforced.
    Hierarchy:
    - Station personnel -> DFO/MFO (active command appointment or District Admin) / Regional Admin / National Admin
    - DFO/MFO -> Regional Fire Officer (active command appointment or Regional Admin) / National Admin
    - RFO / Regional Admin -> National Admin / Superuser
    """
    from gnfs_komenda.models import CommandAppointment

    if not user or not user.is_authenticated:
        return False

    # Officers cannot approve their own leave under ANY condition
    if is_self_approval(user, leave_request):
        return False

    if user.is_superuser:
        return True

    role = get_user_role(user)
    if role == "NATIONAL_ADMIN":
        return True

    personnel = leave_request.personnel
    if not personnel:
        return False

    station = personnel.station
    district = station.district if station else None
    region = district.region if district else None

    user_personnel = NorminalRoll.objects.filter(
        staffID__iexact=user.username
    ).first()

    # Check if applicant is currently an active commanding officer (DFO/MFO)
    applicant_is_co = CommandAppointment.objects.filter(
        personnel=personnel,
        is_active=True,
        appointment_type__in=['DFO', 'MFO', 'METRO_FO', 'ACTING_DFO', 'ACTING_MFO', 'ACTING_METRO_FO']
    ).exists()

    # Check if applicant is currently an active RFO
    applicant_is_rfo = CommandAppointment.objects.filter(
        personnel=personnel,
        is_active=True,
        appointment_type__in=['RFO', 'ACTING_RFO']
    ).exists()

    if applicant_is_rfo:
        # Only National Admin / Superuser can approve RFO leave
        return False

    if applicant_is_co:
        # DFO/MFO leave must escalate to Regional Fire Officer or Regional Admin
        if role == "REGIONAL_ADMIN":
            assignment = get_user_assignment(user)
            return bool(assignment and assignment.active and region and assignment.region_id == region.id)
        if user_personnel and region:
            active_rfo = CommandAppointment.objects.filter(
                region=region,
                is_active=True,
                appointment_type__in=['RFO', 'ACTING_RFO']
            ).first()
            if active_rfo and active_rfo.personnel_id == user_personnel.id:
                return True
        return False

    # Normal station personnel leave approval:
    # 1. Active RFO of region
    if user_personnel and region:
        active_rfo = CommandAppointment.objects.filter(
            region=region,
            is_active=True,
            appointment_type__in=['RFO', 'ACTING_RFO']
        ).first()
        if active_rfo and active_rfo.personnel_id == user_personnel.id:
            return True

    # 2. Regional Admin of region
    if role == "REGIONAL_ADMIN":
        assignment = get_user_assignment(user)
        return bool(assignment and assignment.active and region and assignment.region_id == region.id)

    # 3. Active CO (DFO/MFO) of district
    if user_personnel and district:
        active_co = CommandAppointment.objects.filter(
            district=district,
            is_active=True,
            appointment_type__in=['DFO', 'MFO', 'METRO_FO', 'ACTING_DFO', 'ACTING_MFO', 'ACTING_METRO_FO']
        ).first()
        if active_co and active_co.personnel_id == user_personnel.id:
            return True

    # 4. District Admin / DFO role assignment
    assignment = get_user_assignment(user)
    if assignment and assignment.active:
        if role in ("DFO", "DISTRICT_ADMIN"):
            return bool(district and assignment.district_id == district.id)

    return False



def submit_leave_request(
    personnel,
    leave_type,
    start_date,
    duration,
    end_date=None,
    requested_by=None,
    reason="",
    contact_phone="",
    contact_address="",
    handover_notes="",
    medical_reference="",
    attachment=None,
    is_proportionate=False
):
    """
    Validate, calculate, reserve balance, and create a LeaveRequest atomically.
    """
    # Gender restriction validation
    if hasattr(leave_type, 'allowed_gender'):
        if leave_type.allowed_gender == LeaveType.AllowedGender.MALE and getattr(personnel, 'gender', 'MALE') != NorminalRoll.Gender.MALE:
            raise ValidationError("Female personnel cannot apply for Paternity Leave. Female personnel are entitled to Maternity Leave.")
        if leave_type.allowed_gender == LeaveType.AllowedGender.FEMALE and getattr(personnel, 'gender', 'MALE') != NorminalRoll.Gender.FEMALE:
            raise ValidationError("Male personnel cannot apply for Maternity Leave. Male personnel are entitled to Paternity Leave (5 working days).")

    calc_mode = leave_type.calculation_mode
    calc_result = calculate_leave_dates(
        start_date=start_date,
        duration=duration,
        calculation_mode=calc_mode,
        end_date=end_date
    )

    last_leave_date = calc_result['last_leave_date']
    resume_date = calc_result['resume_date']
    working_days = calc_result['working_days_count']
    calendar_days = calc_result['calendar_days_count']

    # Prevent overlapping active or pending leave requests for this personnel
    overlapping = LeaveRequest.objects.filter(
        personnel=personnel,
        status__in=[
            LeaveRequest.Status.PENDING_APPROVAL,
            LeaveRequest.Status.APPROVED,
            LeaveRequest.Status.ACTIVE
        ],
        start_date__lte=last_leave_date,
        last_leave_date__gte=start_date
    ).exists()

    if overlapping:
        raise ValidationError(
            f"Officer {personnel.staffID} already has a pending or active leave request "
            f"overlapping the period {start_date} to {last_leave_date}."
        )

    leave_year = start_date.year
    entitlement = None
    days_to_consume = working_days if calc_mode == LeaveType.CalculationMode.WORKING_DAYS else duration

    with transaction.atomic():
        if leave_type.requires_entitlement:
            # Lock entitlement row
            entitlement_qs = LeaveEntitlement.objects.select_for_update().filter(
                personnel=personnel,
                leave_year=leave_year,
                leave_type=leave_type
            )
            if not entitlement_qs.exists():
                # Auto-initialize base entitlement based on category
                base = leave_type.default_days
                if leave_type.code == "ANNUAL":
                    category = getattr(personnel, 'officer_category', None)
                    base = 42 if category == "SENIOR" else 36
                entitlement = LeaveEntitlement.objects.create(
                    personnel=personnel,
                    leave_year=leave_year,
                    leave_type=leave_type,
                    base_entitlement=base
                )
            else:
                entitlement = entitlement_qs.first()

            if entitlement.remaining_balance < days_to_consume:
                raise ValidationError(
                    f"Insufficient leave balance. Available: {entitlement.remaining_balance} days, "
                    f"Requested: {days_to_consume} days."
                )

            # Atomically reserve days
            entitlement.days_reserved += days_to_consume
            entitlement.save(update_fields=['days_reserved'])

        leave_req = LeaveRequest.objects.create(
            personnel=personnel,
            entitlement=entitlement,
            leave_type=leave_type,
            calculation_mode=calc_mode,
            start_date=start_date,
            duration=duration,
            is_proportionate=is_proportionate,
            last_leave_date=last_leave_date,
            resume_date=resume_date,
            working_days_count=working_days,
            calendar_days_count=calendar_days,
            status=LeaveRequest.Status.PENDING_APPROVAL,
            requested_by=requested_by,
            reason=reason,
            contact_phone=contact_phone or getattr(personnel, 'contact', '') or "",
            contact_address=contact_address or getattr(personnel, 'residential_address', '') or "",
            handover_notes=handover_notes,
            medical_reference=medical_reference or "",
            attachment=attachment
        )

        record_audit_log(
            user=requested_by,
            action="SUBMIT_LEAVE",
            target_type="LEAVE_REQUEST",
            target_id=leave_req.id,
            description=f"Submitted {leave_type.name} for {personnel.staffID} ({working_days} working days)",
            region=personnel.station.district.region if personnel.station and personnel.station.district else None,
            district=personnel.station.district if personnel.station else None,
            station=personnel.station,
            post=personnel.post
        )

    return leave_req


def approve_leave_request(leave_request_id, user, comments=""):
    """
    Atomically approve a leave request, deduct from entitlement, and sync NorminalRoll.
    """
    with transaction.atomic():
        leave_req = LeaveRequest.objects.select_for_update().select_related(
            'personnel', 'entitlement', 'leave_type', 'personnel__station__district__region'
        ).get(pk=leave_request_id)

        if not can_user_approve_leave(user, leave_req):
            raise PermissionDenied(
                "You do not have permission to approve this leave request or are attempting self-approval."
            )

        if leave_req.status != LeaveRequest.Status.PENDING_APPROVAL:
            raise ValidationError(f"Cannot approve leave with status '{leave_req.get_status_display()}'.")

        prev_status = leave_req.status
        today = timezone.localdate()
        # If start_date <= today <= last_leave_date, mark ACTIVE immediately
        if leave_req.start_date <= today <= leave_req.last_leave_date:
            leave_req.status = LeaveRequest.Status.ACTIVE
        else:
            leave_req.status = LeaveRequest.Status.APPROVED

        leave_req.approved_by = user
        leave_req.approved_at = timezone.now()

        # Update entitlement
        if leave_req.entitlement:
            ent = LeaveEntitlement.objects.select_for_update().get(pk=leave_req.entitlement_id)
            days = leave_req.working_days_count if leave_req.calculation_mode == LeaveType.CalculationMode.WORKING_DAYS else leave_req.duration
            ent.days_reserved = max(0, ent.days_reserved - days)
            ent.days_taken += days
            ent.save(update_fields=['days_reserved', 'days_taken'])

        leave_req.save()

        # Record decision
        approver_role = get_user_role(user)
        LeaveApprovalDecision.objects.create(
            leave_request=leave_req,
            approver=user,
            approver_role=approver_role,
            decision="APPROVED",
            comments=comments,
            previous_status=prev_status,
            new_status=leave_req.status
        )

        # Update operational fields on NorminalRoll
        personnel = leave_req.personnel
        if personnel:
            personnel.leave_type = leave_req.leave_type.name
            personnel.start_leave = leave_req.start_date
            personnel.end_leave = leave_req.last_leave_date
            if leave_req.status == LeaveRequest.Status.ACTIVE:
                personnel.leave_status = "ON LEAVE"
            personnel.called_off = False
            personnel.save(update_fields=['leave_type', 'start_leave', 'end_leave', 'leave_status', 'called_off'])

        record_audit_log(
            user=user,
            action="APPROVE_LEAVE",
            target_type="LEAVE_REQUEST",
            target_id=leave_req.id,
            description=f"Approved leave request #{leave_req.id} for {personnel.staffID}",
            region=personnel.station.district.region if personnel.station and personnel.station.district else None,
            district=personnel.station.district if personnel.station else None,
            station=personnel.station,
            post=personnel.post
        )

    return leave_req


def reject_leave_request(leave_request_id, user, comments=""):
    """
    Atomically reject a leave request and restore any reserved balance.
    """
    with transaction.atomic():
        leave_req = LeaveRequest.objects.select_for_update().select_related(
            'personnel', 'entitlement', 'leave_type'
        ).get(pk=leave_request_id)

        if not can_user_approve_leave(user, leave_req):
            raise PermissionDenied(
                "You do not have permission to reject this leave request or are attempting self-action."
            )

        if leave_req.status != LeaveRequest.Status.PENDING_APPROVAL:
            raise ValidationError(f"Cannot reject leave with status '{leave_req.get_status_display()}'.")

        prev_status = leave_req.status
        leave_req.status = LeaveRequest.Status.REJECTED
        leave_req.rejection_reason = comments

        # Restore reserved days
        if leave_req.entitlement:
            ent = LeaveEntitlement.objects.select_for_update().get(pk=leave_req.entitlement_id)
            days = leave_req.working_days_count if leave_req.calculation_mode == LeaveType.CalculationMode.WORKING_DAYS else leave_req.duration
            ent.days_reserved = max(0, ent.days_reserved - days)
            ent.save(update_fields=['days_reserved'])

        leave_req.save()

        approver_role = get_user_role(user)
        LeaveApprovalDecision.objects.create(
            leave_request=leave_req,
            approver=user,
            approver_role=approver_role,
            decision="REJECTED",
            comments=comments,
            previous_status=prev_status,
            new_status=leave_req.status
        )

        record_audit_log(
            user=user,
            action="REJECT_LEAVE",
            target_type="LEAVE_REQUEST",
            target_id=leave_req.id,
            description=f"Rejected leave request #{leave_req.id} for {leave_req.personnel.staffID}: {comments}",
            station=leave_req.personnel.station,
            post=leave_req.personnel.post
        )

    return leave_req


def _notify_district_stakeholders_on_approval(leave_req, approver_user):
    """
    Prompt 3 Section 13:
    Immediately notify:
    - Personnel
    - Administration
    - Operations
    - District 2IC
    """
    from gnfs_komenda.models import CommandAppointment, AdministrativeAssignment
    from gnfs_komenda.security import send_notification
    from django.contrib.auth import get_user_model
    User = get_user_model()

    district = leave_req.routing_district
    if not district and leave_req.personnel and leave_req.personnel.station:
        district = leave_req.personnel.station.district

    notified_user_ids = {approver_user.id}
    if leave_req.requested_by_id:
        notified_user_ids.add(leave_req.requested_by_id)

    # 1. District 2IC
    if district:
        sec_ic = CommandAppointment.objects.filter(
            district=district,
            appointment_type="DISTRICT_2IC",
            is_active=True
        ).select_related('personnel').first()
        if sec_ic and sec_ic.personnel:
            user_2ic = User.objects.filter(username__iexact=sec_ic.personnel.staffID).first()
            if user_2ic and user_2ic.id not in notified_user_ids:
                send_notification(
                    recipient=user_2ic,
                    title=f"Command Awareness: Leave Approved for {leave_req.personnel.fullname}",
                    message=f"Leave #{leave_req.reference_number or leave_req.id} was command approved ({leave_req.start_date} to {leave_req.last_leave_date}).",
                    notification_type="INFO",
                    link_url=f"/workspace/command/district/{district.id}/"
                )
                notified_user_ids.add(user_2ic.id)

    # 2. District Administration & Operations Stakeholders
    if district:
        admin_assigns = AdministrativeAssignment.objects.filter(
            district=district,
            is_active=True
        ).select_related('personnel')
        for aa in admin_assigns:
            if aa.personnel:
                adm_u = User.objects.filter(username__iexact=aa.personnel.staffID).first()
                if adm_u and adm_u.id not in notified_user_ids:
                    send_notification(
                        recipient=adm_u,
                        title=f"Action Required: Approved Leave #{leave_req.reference_number or leave_req.id}",
                        message=f"Leave for {leave_req.personnel.fullname} was approved by Command. Final administrative dispatch to Watch Room required.",
                        notification_type="ACTION_REQUIRED",
                        link_url=f"/workspace/admin/district/{district.id}/"
                    )
                    notified_user_ids.add(adm_u.id)


def dfo_approve_leave(leave_request_id, user, comments=""):
    """
    DFO / MFO Final Command Approval (Prompt 3 Sections 11-13).
    - Checks anti-self-approval.
    - Resolves active command appointment for the approver.
    - Sets status = LeaveRequest.Status.APPROVED_PENDING_ADMIN_PROCESSING.
    - Records approved_by, approval_appointment, approved_at, approval_comment.
    - Deducts leave balance atomically strictly once (via deduct_leave_balance_atomic).
    - Notifies Personnel, Administration, Operations, and District 2IC.
    """
    from gnfs_komenda.models import CommandAppointment
    from gnfs_komenda.services.leave_balance_service import deduct_leave_balance_atomic
    from gnfs_komenda.security import send_notification

    with transaction.atomic():
        leave_req = LeaveRequest.objects.select_for_update().select_related(
            'personnel', 'entitlement', 'leave_type', 'routing_district', 'routing_region'
        ).get(pk=leave_request_id)

        if is_self_approval(user, leave_req):
            raise PermissionDenied("Self-approval is strictly prohibited. Officers cannot approve their own leave.")

        if not can_user_approve_leave(user, leave_req):
            raise PermissionDenied("You do not have command authority to approve this leave request.")

        valid_pending = (
            LeaveRequest.Status.PENDING_DFO_MFO_DECISION,
            LeaveRequest.Status.PENDING_APPROVAL,
            LeaveRequest.Status.PENDING_OPERATIONS_RECOMMENDATION,
        )
        if leave_req.status not in valid_pending:
            raise ValidationError(
                f"Cannot approve leave with status '{leave_req.get_status_display()}'. Must be awaiting command decision."
            )

        # Resolve command appointment of the approver
        approver_personnel = NorminalRoll.objects.filter(staffID__iexact=user.username).first()
        co_appt = None
        if approver_personnel:
            co_appt = CommandAppointment.objects.filter(
                personnel=approver_personnel,
                is_active=True
            ).first()

        old_status = leave_req.status
        leave_req.status = LeaveRequest.Status.APPROVED_PENDING_ADMIN_PROCESSING
        leave_req.processing_stage = LeaveRequest.ProcessingStage.DISTRICT_ADMIN
        leave_req.approved_by = user
        leave_req.approved_at = timezone.now()
        leave_req.approval_comment = comments
        leave_req.approval_appointment = co_appt

        # Single atomic balance deduction
        deduct_leave_balance_atomic(leave_req)

        leave_req.save()

        # Decision & History
        approver_role = get_user_role(user) or (co_appt.get_appointment_type_display() if co_appt else "Commanding Officer")
        LeaveApprovalDecision.objects.create(
            leave_request=leave_req,
            approver=user,
            approver_role=approver_role,
            decision="APPROVED",
            comments=comments or "Command approval granted by DFO/MFO.",
            previous_status=old_status,
            new_status=leave_req.status
        )

        from gnfs_komenda.models import LeaveWorkflowHistory
        LeaveWorkflowHistory.objects.create(
            leave_request=leave_req,
            action="COMMAND_APPROVED",
            from_status=old_status,
            to_status=leave_req.status,
            actor=user,
            actor_role=approver_role,
            functional_office="Command",
            comment=comments or "Command approval granted by DFO/MFO."
        )

        # Notify Personnel
        if leave_req.requested_by:
            send_notification(
                recipient=leave_req.requested_by,
                title="Leave Command Approved",
                message=f"Your {leave_req.leave_type.name} request #{leave_req.reference_number or leave_req.id} has been APPROVED by Command. Pending final administrative processing.",
                notification_type="SUCCESS",
                link_url=f"/my/leave/{leave_req.id}/"
            )

        _notify_district_stakeholders_on_approval(leave_req, user)

        return leave_req


def dfo_reject_leave(leave_request_id, user, comments=""):
    """
    DFO / MFO Final Command Rejection (Prompt 3 Sections 11-12).
    """
    from gnfs_komenda.services.leave_balance_service import release_reserved_leave_days
    from gnfs_komenda.security import send_notification

    if not comments or not comments.strip():
        raise ValidationError("A written rejection reason is mandatory.")

    with transaction.atomic():
        leave_req = LeaveRequest.objects.select_for_update().select_related(
            'personnel', 'entitlement', 'leave_type', 'routing_district', 'routing_region'
        ).get(pk=leave_request_id)

        if is_self_approval(user, leave_req):
            raise PermissionDenied("Self-action is strictly prohibited.")

        if not can_user_approve_leave(user, leave_req):
            raise PermissionDenied("You do not have command authority to reject this leave request.")

        valid_pending = (
            LeaveRequest.Status.PENDING_DFO_MFO_DECISION,
            LeaveRequest.Status.PENDING_APPROVAL,
            LeaveRequest.Status.PENDING_OPERATIONS_RECOMMENDATION,
        )
        if leave_req.status not in valid_pending:
            raise ValidationError(f"Cannot reject leave with status '{leave_req.get_status_display()}'.")

        old_status = leave_req.status
        leave_req.status = LeaveRequest.Status.REJECTED
        leave_req.rejection_reason = comments
        leave_req.save(update_fields=['status', 'rejection_reason', 'updated_at'])

        # Release reserved balance
        release_reserved_leave_days(leave_req)

        approver_role = get_user_role(user) or "Commanding Officer"
        LeaveApprovalDecision.objects.create(
            leave_request=leave_req,
            approver=user,
            approver_role=approver_role,
            decision="REJECTED",
            comments=comments,
            previous_status=old_status,
            new_status=leave_req.status
        )

        from gnfs_komenda.models import LeaveWorkflowHistory
        LeaveWorkflowHistory.objects.create(
            leave_request=leave_req,
            action="COMMAND_REJECTED",
            from_status=old_status,
            to_status=leave_req.status,
            actor=user,
            actor_role=approver_role,
            functional_office="Command",
            comment=comments
        )

        if leave_req.requested_by:
            send_notification(
                recipient=leave_req.requested_by,
                title="Leave Request Rejected",
                message=f"Your leave request #{leave_req.id} was rejected by Command: {comments}",
                notification_type="DANGER",
                link_url=f"/my/leave/{leave_req.id}/"
            )

        return leave_req
