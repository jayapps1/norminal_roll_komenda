"""
Leave Approval & Workflow Service for GNFS.
Implements tiered hierarchical approvals, strict anti-self-approval enforcement,
atomic balance reservation/deduction with select_for_update(), and sync with NorminalRoll.
"""

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
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


def can_user_approve_leave(user, leave_request):
    """
    Determine if a user has sufficient hierarchical jurisdiction to approve a leave request.
    Anti-self-approval is strictly enforced.
    Hierarchy:
    - Station personnel -> DFO (District Fire Officer) / Regional Admin / National Admin
    - DFO -> Regional Fire Officer (RFO) / National Admin
    - RFO / Regional Admin -> National Admin / Superuser
    """
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

    assignment = get_user_assignment(user)
    if not assignment or not assignment.active:
        return False

    personnel = leave_request.personnel
    if not personnel:
        return False

    station = personnel.station
    if not station:
        # Personnel not assigned to any station -> only National Admin can approve
        return False

    district = station.district
    region = district.region if district else None

    # Regional Admin can approve any station or district personnel within their region
    if role == "REGIONAL_ADMIN":
        return region and assignment.region_id == region.id

    # DFO / District Admin can approve station/post personnel in their district
    if role in ("DFO", "DISTRICT_ADMIN"):
        return district and assignment.district_id == district.id

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
