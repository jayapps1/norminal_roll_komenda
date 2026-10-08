"""
Leave Routing and Multi-Tier Workflow Service for GNFS.
Implements the full administrative review and command approval pipeline:
1. Personnel submits -> PENDING_PROCESSING
2. Administrative Processing (District Admin / Regional Admin) -> Reviews, Checks Documents & Balances
   - Forward to Command Approver -> PENDING_APPROVAL
   - Return for Correction -> RETURNED_FOR_CORRECTION
3. Command Approval (Current active DFO/MFO / RFO / Higher Authority)
   - Revalidates all calculations, holidays, overlaps, and current posting
   - Approve -> Deducts Entitlement, updates status to APPROVED / ACTIVE
   - Reject -> Releases reserved balance, preserves historical audit
   - Return for Correction -> RETURNED_FOR_CORRECTION
4. Resubmit -> Back to PENDING_PROCESSING

Strict Rules:
- applicant != approver (Strict Anti-Self-Approval across all roles).
- DFO/MFO personal leave routes to Regional Admin and current RFO of their Region.
- RFO personal leave routes to Higher Approval Authority (National Admin/HQ).
- Department / Unit membership does NOT change command approval hierarchy.
- Command titles and authorities are resolved dynamically from active CommandAppointment records.
"""

from datetime import datetime, date
from django.conf import settings
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone

from gnfs_komenda.models import (
    LeaveRequest, LeaveEntitlement, LeaveType, LeaveApprovalDecision,
    WatchRoomBooking, LeaveWorkflowHistory, FunctionalOffice,
    NorminalRoll, CommandAppointment, District, Region, AuditLog, Notification
)
from gnfs_komenda.services.leave_calculator import calculate_leave_dates
from gnfs_komenda.security import get_user_role, get_user_assignment, get_personnel_for_user, send_notification


def resolve_leave_route(personnel):
    """
    Examine personnel's current command posting and active command appointments
    to determine the required Administrative Processor and Final Command Approver.
    """
    posting = getattr(personnel, 'current_posting', None)
    if posting:
        region = posting.region
        district = posting.district
        station = posting.station
    else:
        station = personnel.station
        district = station.district if station else None
        region = district.region if district else (personnel.current_region if hasattr(personnel, 'current_region') else None)

    # Check if applicant currently holds an active CommandAppointment
    active_appt = CommandAppointment.objects.filter(
        personnel=personnel,
        is_active=True
    ).select_related('region', 'district').first()

    # Rule 1: Applicant is Regional Fire Officer (RFO / Acting RFO)
    if active_appt and active_appt.appointment_type in ('RFO', 'ACTING_RFO'):
        return {
            "applicant_type": "REGIONAL_FIRE_OFFICER",
            "applicant_appointment": active_appt,
            "administrative_role": "NATIONAL_ADMIN",
            "processor_stage": LeaveRequest.ProcessingStage.HIGHER_AUTHORITY,
            "administrative_region": None,
            "region": None,
            "administrative_district": None,
            "district": None,
            "final_approver_type": "HIGHER_AUTHORITY",
            "final_approver_role": "NATIONAL_ADMIN",
            "approver_appointment": None,
            "approver_officer": None,
            "approver_region": None,
            "approver_district": None,
            "escalated": True,
            "escalation_reason": "Applicant holds active RFO command appointment. Routed to National Authority."
        }

    # Rule 2: Applicant is District / Municipal Command Leader (DFO / MFO / Metro FO)
    if active_appt and active_appt.appointment_type in (
        'DFO', 'MFO', 'METRO_FO', 'ACTING_DFO', 'ACTING_MFO', 'ACTING_METRO_FO'
    ):
        parent_region = active_appt.region or (district.region if district else region)
        rfo_appt = parent_region.current_rfo if parent_region else None
        return {
            "applicant_type": "COMMANDING_OFFICER",
            "applicant_appointment": active_appt,
            "administrative_role": "REGIONAL_ADMIN",
            "processor_stage": LeaveRequest.ProcessingStage.REGIONAL_ADMIN,
            "administrative_region": parent_region,
            "region": parent_region,
            "administrative_district": None,
            "district": None,
            "final_approver_type": "RFO",
            "final_approver_role": "REGIONAL_ADMIN",
            "approver_appointment": rfo_appt,
            "approver_officer": rfo_appt,
            "approver_region": parent_region,
            "approver_district": None,
            "escalated": True,
            "escalation_reason": "Applicant holds active DFO/MFO command appointment. Routed to Regional Admin and RFO."
        }

    # Rule 3: Personnel posted directly under Regional Command (no District)
    if not district and region:
        rfo_appt = region.current_rfo
        return {
            "applicant_type": "REGIONAL_STAFF",
            "applicant_appointment": active_appt,
            "administrative_role": "REGIONAL_ADMIN",
            "processor_stage": LeaveRequest.ProcessingStage.REGIONAL_ADMIN,
            "administrative_region": region,
            "region": region,
            "administrative_district": None,
            "district": None,
            "final_approver_type": "RFO",
            "final_approver_role": "REGIONAL_ADMIN",
            "approver_appointment": rfo_appt,
            "approver_officer": rfo_appt,
            "approver_region": region,
            "approver_district": None,
            "escalated": False,
            "escalation_reason": ""
        }

    # Rule 4: Ordinary District / Station Personnel (including District 2IC)
    # Note: 2IC does not automatically final-approve or bypass unless appointed as acting CO
    co_appt = district.current_commanding_officer if district else None
    return {
        "applicant_type": "DISTRICT_STAFF",
        "applicant_appointment": active_appt,
        "administrative_role": "DISTRICT_ADMIN",
        "processor_stage": LeaveRequest.ProcessingStage.DISTRICT_ADMIN,
        "administrative_region": region,
        "region": region,
        "administrative_district": district,
        "district": district,
        "final_approver_type": "DFO_MFO",
        "final_approver_role": "DFO",
        "approver_appointment": co_appt,
        "approver_officer": co_appt,
        "approver_region": region,
        "approver_district": district,
        "escalated": False,
        "escalation_reason": ""
    }


def is_operations_personnel(personnel):
    """
    Prompt 3 Section 5 & 29:
    Determines if the personnel officer has an active functional assignment to OPERATIONS.
    Inspects:
    1. PersonnelFunctionalAssignment (is_active=True)
       - functional_office__function_type='OPERATIONS'
       - functional_unit with code='OPS' or 'operation' in name (or ancestor)
    2. FunctionalOfficeMember (is_active=True, functional_office__function_type='OPERATIONS')
    3. AdministrativeAssignment (is_active=True, functional_office__function_type='OPERATIONS')
    4. FunctionalRoleAssignment (active=True, functional_office__function_type='OPERATIONS')
    5. Direct functional_unit on personnel
    """
    if not personnel:
        return False

    from django.db.models import Q
    from gnfs_komenda.models import (
        PersonnelFunctionalAssignment,
        AdministrativeAssignment,
        FunctionalRoleAssignment,
        FunctionalOffice,
    )

    # 1. PersonnelFunctionalAssignment
    has_pfa = PersonnelFunctionalAssignment.objects.filter(
        personnel=personnel,
        is_active=True
    ).filter(
        Q(functional_office__function_type=FunctionalOffice.FunctionType.OPERATIONS) |
        Q(functional_unit__code__iexact="OPS") |
        Q(functional_unit__name__icontains="operation") |
        Q(functional_unit__parent__code__iexact="OPS") |
        Q(functional_unit__parent__name__icontains="operation")
    ).exists()
    if has_pfa:
        return True

    # 3. AdministrativeAssignment (FUNCTIONAL)
    has_adm = AdministrativeAssignment.objects.filter(
        personnel=personnel,
        is_active=True,
        functional_office__function_type=FunctionalOffice.FunctionType.OPERATIONS
    ).exists()
    if has_adm:
        return True

    # 4. FunctionalRoleAssignment
    has_fra = FunctionalRoleAssignment.objects.filter(
        personnel=personnel,
        active=True,
        functional_office__function_type=FunctionalOffice.FunctionType.OPERATIONS
    ).exists()
    if has_fra:
        return True

    # 5. Direct cached functional_unit on NorminalRoll
    if personnel.functional_unit:
        u = personnel.functional_unit
        if getattr(u, 'code', '').upper() == 'OPS' or 'operation' in getattr(u, 'name', '').lower():
            return True
        if u.parent and (getattr(u.parent, 'code', '').upper() == 'OPS' or 'operation' in getattr(u.parent, 'name', '').lower()):
            return True

    return False


def can_user_process_leave(user, leave_req):
    """
    Check if user is authorized to administratively review, verify, and forward
    this specific leave request.
    Strict Anti-Self-Processing: An officer cannot process their own leave request.
    """
    if not user or not user.is_authenticated or not leave_req:
        return False

    if leave_req.requested_by_id and leave_req.requested_by_id == user.id:
        return False
    user_personnel = get_personnel_for_user(user)
    if user_personnel and user_personnel.id == leave_req.personnel_id:
        return False  # Self-processing forbidden

    if user.is_superuser:
        return True

    user_role = get_user_role(user)
    assignment = get_user_assignment(user)

    if user_role == "NATIONAL_ADMIN":
        return True

    stage = leave_req.processing_stage

    if stage == LeaveRequest.ProcessingStage.DISTRICT_ADMIN:
        if user_role in ("DISTRICT_ADMIN", "DFO"):
            if assignment and assignment.active and leave_req.routing_district_id:
                return assignment.district_id == leave_req.routing_district_id
        if user_role == "REGIONAL_ADMIN":
            if assignment and assignment.active and leave_req.routing_region_id:
                return assignment.region_id == leave_req.routing_region_id

    elif stage == LeaveRequest.ProcessingStage.REGIONAL_ADMIN:
        if user_role == "REGIONAL_ADMIN":
            if assignment and assignment.active and leave_req.routing_region_id:
                return assignment.region_id == leave_req.routing_region_id

    # Functional Scope Resolver check
    from gnfs_komenda.services.functional_scope_service import get_functional_scope
    scope = get_functional_scope(user, "ADMINISTRATION")
    if scope["authorized"]:
        if scope["level"] == "NATIONAL":
            return True
        if scope["level"] == "REGIONAL" and leave_req.routing_region_id and scope["region"]:
            if leave_req.routing_region_id == scope["region"].id:
                return True
        if scope["level"] == "DISTRICT" and leave_req.routing_district_id and scope["district"]:
            if leave_req.routing_district_id == scope["district"].id:
                return True

    return False


def can_user_recommend_leave(user, leave_req):
    """
    Check if user is authorized to submit Operations recommendations on this leave request (Section 48).
    Strict anti-self-recommendation.
    """
    if not user or not user.is_authenticated or not leave_req:
        return False
    if leave_req.requested_by_id and leave_req.requested_by_id == user.id:
        return False
    user_personnel = get_personnel_for_user(user)
    if user_personnel and user_personnel.id == leave_req.personnel_id:
        return False
    if user.username.upper() == leave_req.personnel.staffID.upper():
        return False
    if user.is_superuser:
        return True

    from gnfs_komenda.services.functional_scope_service import get_functional_scope
    scope = get_functional_scope(user, "OPERATIONS")
    if scope["authorized"]:
        if scope["level"] == "NATIONAL":
            return True
        if scope["level"] == "REGIONAL" and leave_req.routing_region_id and scope["region"]:
            if leave_req.routing_region_id == scope["region"].id:
                return True
        if scope["level"] == "DISTRICT" and leave_req.routing_district_id and scope["district"]:
            if leave_req.routing_district_id == scope["district"].id:
                return True

    # District Admin fallback
    if can_user_process_leave(user, leave_req):
        return True
    return False


def can_user_release_to_watch_room(user, leave_req):
    """
    Check if user has Administration authority to release approved leave to Watch Room (Section 52-54).
    """
    return can_user_process_leave(user, leave_req)


def can_user_book_watch_room(user, leave_req):
    """
    Check if user has Watch Room operational logging authority (Section 40-43, 54).
    Strict anti-self-booking.
    """
    if not user or not user.is_authenticated or not leave_req:
        return False
    if leave_req.requested_by_id and leave_req.requested_by_id == user.id:
        return False
    user_personnel = get_personnel_for_user(user)
    if user_personnel and user_personnel.id == leave_req.personnel_id:
        return False
    if user.username.upper() == leave_req.personnel.staffID.upper():
        return False
    if user.is_superuser:
        return True

    from gnfs_komenda.services.functional_scope_service import get_functional_scope
    scope = get_functional_scope(user, "WATCH_ROOM")
    if scope["authorized"]:
        if scope["level"] == "NATIONAL":
            return True
        if scope["district"] and leave_req.routing_district_id == scope["district"].id:
            return True

    # Operations / Administration fallback
    if can_user_process_leave(user, leave_req):
        return True
    return False


def can_user_final_approve_leave(user, leave_req):
    """
    Check if user is the authorized executive command approver for this leave request.
    Enforces Universal Anti-Self-Approval and dynamic command appointment resolution.
    """
    if not user or not user.is_authenticated or not leave_req:
        return False

    user_personnel = get_personnel_for_user(user)

    # Universal Rule: Applicant cannot approve their own leave
    if leave_req.requested_by_id and leave_req.requested_by_id == user.id:
        return False
    if user_personnel and user_personnel.id == leave_req.personnel_id:
        return False
    if user.username.upper() == leave_req.personnel.staffID.upper():
        return False

    if user.is_superuser:
        return True

    route = resolve_leave_route(leave_req.personnel)
    approver_type = route["final_approver_type"]

    # 1. Higher Authority Route (RFO personal leave)
    if approver_type == "HIGHER_AUTHORITY":
        return get_user_role(user) == "NATIONAL_ADMIN" or user.is_staff

    # 2. Regional Fire Officer Route (Regional staff or DFO/MFO applicant)
    elif approver_type == "RFO":
        target_region = route["approver_region"] or leave_req.routing_region
        if not target_region:
            return False
        # Active RFO appointment check
        if target_region.current_rfo and user_personnel and target_region.current_rfo.personnel_id == user_personnel.id:
            return True
        # Assignment fallback
        assignment = get_user_assignment(user)
        if assignment and assignment.active and assignment.role == "REGIONAL_ADMIN":
            return assignment.region_id == target_region.id

    # 3. District / Municipal Fire Officer Route (DFO / MFO)
    elif approver_type == "DFO_MFO":
        target_district = route["approver_district"] or leave_req.routing_district
        if not target_district:
            return False
        # Active DFO / MFO appointment check
        if target_district.current_commanding_officer and user_personnel and target_district.current_commanding_officer.personnel_id == user_personnel.id:
            return True
        # Assignment fallback: only DFO role (not District Admin)
        assignment = get_user_assignment(user)
        if assignment and assignment.active and assignment.role == "DFO":
            return assignment.district_id == target_district.id
        # Regional Admin of that region can act if authorized
        if assignment and assignment.active and assignment.role == "REGIONAL_ADMIN" and target_district.region_id:
            return assignment.region_id == target_district.region_id

    return False


def submit_personnel_leave_request(personnel, leave_type, start_date, duration,
                                   requested_by, reason="", contact_phone="",
                                   contact_address="", handover_notes="",
                                   medical_reference="", attachment=None,
                                   is_proportionate=False, is_admin_created=False):
    """
    Initiate a new personnel leave request (Section 44-46).
    Supports officer self-service and authorized Administration creation.
    Calculates dates, reserves entitlement, determines routing, and logs initial submission.
    """
    # 0. Authoritative Sex / Gender & Duration Validation (Section 5, 7, 14, 16, 26)
    from gnfs_komenda.services.leave_policy_service import validate_leave_eligibility_backend, get_base_annual_entitlement
    duration, calc_mode = validate_leave_eligibility_backend(personnel, leave_type, duration)

    if duration <= 0:
        raise ValidationError("Leave duration must be greater than zero.")

    # 1. Prevent duplicate concurrent submissions
    pending_exists = LeaveRequest.objects.filter(
        personnel=personnel,
        status__in=[
            LeaveRequest.Status.PENDING_PROCESSING,
            LeaveRequest.Status.PENDING_ADMIN_REVIEW,
            LeaveRequest.Status.PENDING_OPERATIONS_RECOMMENDATION,
            LeaveRequest.Status.PENDING_APPROVAL,
        ]
    ).exists()
    if pending_exists:
        raise ValidationError("You already have an active leave request currently awaiting processing or approval.")

    # 2. Check for overlapping approved / active leave
    date_calc = calculate_leave_dates(
        start_date=start_date,
        duration=duration,
        calculation_mode=calc_mode
    )
    last_leave_date = date_calc['last_leave_date']
    resume_date = date_calc['resume_date']

    overlapping = LeaveRequest.objects.filter(
        personnel=personnel,
        status__in=[
            LeaveRequest.Status.APPROVED,
            LeaveRequest.Status.ACTIVE,
            LeaveRequest.Status.BOOKED,
            LeaveRequest.Status.PENDING_ADMIN_FINAL_PROCESSING,
            LeaveRequest.Status.SENT_TO_WATCH_ROOM,
        ],
        start_date__lte=last_leave_date,
        last_leave_date__gte=start_date
    ).exists()
    if overlapping:
        raise ValidationError("The requested dates overlap with an existing approved or active leave period.")

    # 3. Check and reserve entitlement if applicable
    entitlement = None
    year = start_date.year

    if leave_type.code == "ANNUAL":
        base_annual = get_base_annual_entitlement(personnel.officer_category)
        entitlement, _ = LeaveEntitlement.objects.get_or_create(
            personnel=personnel,
            leave_year=year,
            leave_type=leave_type,
            defaults={
                'base_entitlement': base_annual
            }
        )
        if entitlement.remaining_balance < duration:
            raise ValidationError(
                f"Insufficient leave balance. Requested {duration} days, but only {entitlement.remaining_balance} days available."
            )
        # Atomically reserve days
        entitlement.days_reserved += duration
        entitlement.save(update_fields=['days_reserved', 'updated_at'])

    # 4. Resolve multi-tier routing
    route = resolve_leave_route(personnel)

    with transaction.atomic():
        leave_req = LeaveRequest.objects.create(
            personnel=personnel,
            entitlement=entitlement,
            leave_type=leave_type,
            leave_year=year,
            officer_category_snapshot=personnel.officer_category,
            rank_snapshot=personnel.rank,
            start_date=start_date,
            duration=duration,
            is_proportionate=is_proportionate,
            calculation_mode=calc_mode,
            last_leave_date=last_leave_date,
            resume_date=resume_date,
            working_days_count=date_calc['working_days_count'],
            calendar_days_count=date_calc['calendar_days_count'],
            reason=reason,
            contact_phone=contact_phone,
            contact_address=contact_address,
            handover_notes=handover_notes,
            medical_reference=medical_reference,
            attachment=attachment,
            creation_mode='ADMIN' if is_admin_created else 'PERSONNEL',
            status=LeaveRequest.Status.PENDING_ADMIN_REVIEW,
            processing_stage=route['administrative_role'],
            routing_district=route['administrative_district'],
            routing_region=route['administrative_region'],
            requested_by=requested_by
        )

        LeaveApprovalDecision.objects.create(
            leave_request=leave_req,
            approver=requested_by,
            approver_role="ADMINISTRATION" if is_admin_created else "APPLICANT",
            decision="SUBMITTED",
            comments=f"Leave request submitted for {duration} days ({leave_type.name}).",
            previous_status=LeaveRequest.Status.DRAFT,
            new_status=leave_req.status
        )

        LeaveWorkflowHistory.objects.create(
            leave_request=leave_req,
            action="LEAVE_CREATED_BY_ADMIN" if is_admin_created else "LEAVE_SUBMITTED",
            from_status="",
            to_status=leave_req.status,
            actor=requested_by,
            actor_role="Administration" if is_admin_created else "Personnel",
            functional_office="Administration" if is_admin_created else "Personnel",
            comment=f"Leave request initiated for {duration} days ({leave_type.name})."
        )

        # Notify administrative processors
        dest_str = route['administrative_district'].name if route['administrative_district'] else (
            route['administrative_region'].name if route['administrative_region'] else "National HQ"
        )
        AuditLog.objects.create(
            user=requested_by,
            action="LEAVE_CREATED_BY_ADMIN" if is_admin_created else "LEAVE_SUBMITTED",
            target_type="LeaveRequest",
            target_id=str(leave_req.id),
            description=f"Initiated {leave_type.name} request for {personnel.fullname} ({personnel.staffID}), routed to {dest_str}.",
            region=route['administrative_region'],
            district=route['administrative_district'],
            station=personnel.station,
            post=personnel.post
        )

        # Notify applicant
        if requested_by:
            send_notification(
                recipient=requested_by,
                title="Leave Request Submitted",
                message=f"Your {leave_type.name} request for {duration} days starting {start_date} was submitted and is awaiting administrative processing.",
                notification_type="INFO",
                link_url=f"/my/leave/{leave_req.id}/"
            )

    return leave_req


def process_leave_request(leave_req_id, user, action, comments=""):
    """
    Administrative Processing step (District Admin or Regional Admin) - Section 44, 47, 53.
    action can be:
    - 'FORWARD' or 'FORWARD_TO_OPERATIONS': Forwards verified request to Operations for recommendation.
    - 'RETURN_FOR_CORRECTION': Returns request to applicant with required changes.
    """
    with transaction.atomic():
        leave_req = LeaveRequest.objects.select_for_update().select_related(
            'personnel', 'leave_type', 'entitlement', 'routing_district', 'routing_region'
        ).get(pk=leave_req_id)

        if not can_user_process_leave(user, leave_req):
            raise PermissionDenied("You are not authorized to administratively process this leave request.")

        if leave_req.status not in (
            LeaveRequest.Status.PENDING_PROCESSING,
            LeaveRequest.Status.PENDING_ADMIN_REVIEW,
            LeaveRequest.Status.SUBMITTED
        ):
            raise ValidationError(f"Request is in status '{leave_req.get_status_display()}'; only pending review requests can be processed.")

        user_role = get_user_role(user)
        old_status = leave_req.status

        if action in ("FORWARD", "FORWARD_TO_OPERATIONS"):
            if action == "FORWARD_TO_OPERATIONS":
                leave_req.status = LeaveRequest.Status.PENDING_OPERATIONS_RECOMMENDATION
                leave_req.processing_stage = LeaveRequest.ProcessingStage.DISTRICT_OPERATIONS if leave_req.routing_district else LeaveRequest.ProcessingStage.REGIONAL_OPERATIONS
                decision_code = "FORWARDED_TO_OPERATIONS"
                history_action = "ADMIN_REVIEWED_AND_FORWARDED"
            else:
                has_ops = False
                if leave_req.routing_district:
                    has_ops = FunctionalOffice.objects.filter(
                        district=leave_req.routing_district,
                        function_type=FunctionalOffice.FunctionType.OPERATIONS,
                        is_established=True,
                        active=True
                    ).exists()
                elif leave_req.routing_region:
                    has_ops = FunctionalOffice.objects.filter(
                        region=leave_req.routing_region,
                        function_type=FunctionalOffice.FunctionType.OPERATIONS,
                        is_established=True,
                        active=True
                    ).exists()

                if has_ops:
                    leave_req.status = LeaveRequest.Status.PENDING_OPERATIONS_RECOMMENDATION
                    leave_req.processing_stage = LeaveRequest.ProcessingStage.DISTRICT_OPERATIONS if leave_req.routing_district else LeaveRequest.ProcessingStage.REGIONAL_OPERATIONS
                    decision_code = "FORWARDED_TO_OPERATIONS"
                    history_action = "ADMIN_REVIEWED_AND_FORWARDED"
                else:
                    leave_req.status = LeaveRequest.Status.PENDING_APPROVAL
                    leave_req.processing_stage = LeaveRequest.ProcessingStage.COMMAND_APPROVAL
                    decision_code = "FORWARDED"
                    history_action = "FORWARDED_TO_COMMAND"

            leave_req.processed_by = user
            leave_req.processed_at = timezone.now()
            leave_req.processing_notes = comments
            leave_req.save(update_fields=['status', 'processing_stage', 'processed_by', 'processed_at', 'processing_notes', 'updated_at'])

            LeaveApprovalDecision.objects.create(
                leave_request=leave_req,
                approver=user,
                approver_role="ADMINISTRATION",
                decision=decision_code,
                comments=comments or "Verified and forwarded.",
                previous_status=old_status,
                new_status=leave_req.status
            )

            LeaveWorkflowHistory.objects.create(
                leave_request=leave_req,
                action=history_action,
                from_status=old_status,
                to_status=leave_req.status,
                actor=user,
                actor_role="Administration",
                functional_office="Administration",
                comment=comments or "Administrative review complete."
            )

            # Audit
            AuditLog.objects.create(
                user=user,
                action="LEAVE_ADMIN_REVIEWED",
                target_type="LeaveRequest",
                target_id=str(leave_req.id),
                description=f"Administration processed leave request for {leave_req.personnel.fullname}.",
                region=leave_req.routing_region,
                district=leave_req.routing_district
            )

            # Notify applicant
            if leave_req.requested_by:
                send_notification(
                    recipient=leave_req.requested_by,
                    title="Leave Request Verified",
                    message=f"Your {leave_req.leave_type.name} request has been verified by Administration.",
                    link_url=f"/my/leave/{leave_req.id}/"
                )

        elif action == "RETURN_FOR_CORRECTION":
            if not comments.strip():
                raise ValidationError("Please provide a reason/instruction when returning a request for correction.")

            leave_req.status = LeaveRequest.Status.RETURNED_FOR_CORRECTION
            leave_req.correction_reason = comments
            leave_req.processed_by = user
            leave_req.processed_at = timezone.now()
            leave_req.save(update_fields=['status', 'correction_reason', 'processed_by', 'processed_at', 'updated_at'])

            LeaveApprovalDecision.objects.create(
                leave_request=leave_req,
                approver=user,
                approver_role="ADMINISTRATION",
                decision="RETURNED_FOR_CORRECTION",
                comments=comments,
                previous_status=old_status,
                new_status=leave_req.status
            )

            LeaveWorkflowHistory.objects.create(
                leave_request=leave_req,
                action="RETURNED_FOR_CORRECTION",
                from_status=old_status,
                to_status=leave_req.status,
                actor=user,
                actor_role="Administration",
                functional_office="Administration",
                comment=comments
            )

            # Notify applicant
            if leave_req.requested_by:
                send_notification(
                    recipient=leave_req.requested_by,
                    title="Leave Request Returned for Correction",
                    message=f"Your {leave_req.leave_type.name} request was returned for correction: {comments}",
                    link_url=f"/my/leave/{leave_req.id}/edit/"
                )

        else:
            raise ValidationError(f"Invalid processing action '{action}'.")

        return leave_req


def operations_recommend_leave_request(leave_req_id, user, recommendation, comments=""):
    """
    Operations Recommendation step (Section 48-49).
    Operations does NOT finally approve leave. Operations makes an advisory recommendation.
    recommendation can be:
    - 'RECOMMEND': Operations recommends approval based on operational strength.
    - 'NOT_RECOMMEND': Operations does not recommend approval.
    - 'RETURN_FOR_CORRECTION': Operations returns request for correction.
    """
    with transaction.atomic():
        leave_req = LeaveRequest.objects.select_for_update().select_related(
            'personnel', 'leave_type', 'entitlement', 'routing_district', 'routing_region'
        ).get(pk=leave_req_id)

        if not can_user_recommend_leave(user, leave_req):
            raise PermissionDenied("You are not authorized to make Operations recommendations for this leave request.")

        if leave_req.status != LeaveRequest.Status.PENDING_OPERATIONS_RECOMMENDATION:
            raise ValidationError(f"Request is in status '{leave_req.get_status_display()}'; only requests awaiting Operations recommendation can be processed here.")

        old_status = leave_req.status

        if recommendation in ("RECOMMEND", "NOT_RECOMMEND"):
            leave_req.operations_recommendation = recommendation
            leave_req.operations_recommended_by = user
            leave_req.operations_recommended_at = timezone.now()
            leave_req.operations_comments = comments

            # Moves to Command Approver (DFO/MFO or RFO)
            leave_req.status = LeaveRequest.Status.PENDING_APPROVAL
            leave_req.processing_stage = LeaveRequest.ProcessingStage.COMMAND_APPROVAL
            leave_req.save(update_fields=[
                'operations_recommendation', 'operations_recommended_by',
                'operations_recommended_at', 'operations_comments',
                'status', 'processing_stage', 'updated_at'
            ])

            rec_label = "Recommended" if recommendation == "RECOMMEND" else "Not Recommended"
            rec_action = "OPERATIONS_RECOMMENDED" if recommendation == "RECOMMEND" else "OPERATIONS_NOT_RECOMMENDED"

            LeaveApprovalDecision.objects.create(
                leave_request=leave_req,
                approver=user,
                approver_role="OPERATIONS",
                decision=rec_action,
                comments=comments or f"Operations {rec_label.lower()} for command approval.",
                previous_status=old_status,
                new_status=leave_req.status
            )

            LeaveWorkflowHistory.objects.create(
                leave_request=leave_req,
                action=rec_action,
                from_status=old_status,
                to_status=leave_req.status,
                actor=user,
                actor_role="Operations",
                functional_office="Operations",
                comment=comments or f"Operations {rec_label}."
            )

            # Audit
            AuditLog.objects.create(
                user=user,
                action=rec_action,
                target_type="LeaveRequest",
                target_id=str(leave_req.id),
                description=f"Operations ({rec_label}) leave request for {leave_req.personnel.fullname}. Comment: {comments}",
                region=leave_req.routing_region,
                district=leave_req.routing_district
            )

            # Notify applicant
            if leave_req.requested_by:
                send_notification(
                    recipient=leave_req.requested_by,
                    title="Leave Request Operations Recommendation Complete",
                    message=f"Operations has submitted their recommendation ({rec_label}) on your leave request. It is now awaiting Command Decision.",
                    link_url=f"/my/leave/{leave_req.id}/"
                )

        elif recommendation == "RETURN_FOR_CORRECTION":
            if not comments.strip():
                raise ValidationError("Please provide a reason/instruction when returning a request for correction.")

            leave_req.status = LeaveRequest.Status.RETURNED_FOR_CORRECTION
            leave_req.correction_reason = comments
            leave_req.operations_recommended_by = user
            leave_req.operations_recommended_at = timezone.now()
            leave_req.operations_comments = comments
            leave_req.save(update_fields=['status', 'correction_reason', 'operations_recommended_by', 'operations_recommended_at', 'operations_comments', 'updated_at'])

            LeaveApprovalDecision.objects.create(
                leave_request=leave_req,
                approver=user,
                approver_role="OPERATIONS",
                decision="RETURNED_FOR_CORRECTION",
                comments=comments,
                previous_status=old_status,
                new_status=leave_req.status
            )

            LeaveWorkflowHistory.objects.create(
                leave_request=leave_req,
                action="OPERATIONS_RETURNED_FOR_CORRECTION",
                from_status=old_status,
                to_status=leave_req.status,
                actor=user,
                actor_role="Operations",
                functional_office="Operations",
                comment=comments
            )

            if leave_req.requested_by:
                send_notification(
                    recipient=leave_req.requested_by,
                    title="Leave Request Returned by Operations",
                    message=f"Operations returned your leave request for correction: {comments}",
                    link_url=f"/my/leave/{leave_req.id}/edit/"
                )

        else:
            raise ValidationError(f"Invalid recommendation action '{recommendation}'.")

        return leave_req


def final_approve_leave_request(leave_req_id, user, comments=""):
    """
    Final Executive Command Approval (DFO / MFO / RFO / Higher Authority) - Section 50-53.
    DFO/MFO makes final decision regardless of Operations recommendation (advisory).
    Strictly revalidates available balance, dates, public holidays, and anti-self-approval.
    Atomically deducts balance and returns to Administration for final processing (Section 52).
    """
    with transaction.atomic():
        leave_req = LeaveRequest.objects.select_for_update().select_related(
            'personnel', 'leave_type', 'entitlement', 'routing_district', 'routing_region'
        ).get(pk=leave_req_id)

        # 1. Strict authorization & anti-self-approval check
        if not can_user_final_approve_leave(user, leave_req):
            raise PermissionDenied("You are not authorized to approve this leave request, or self-approval was attempted.")

        # 2. State check (Section 51: DFO has command authority to approve even if Operations recommendation is pending)
        valid_approval_statuses = (
            LeaveRequest.Status.PENDING_APPROVAL,
            LeaveRequest.Status.PENDING_OPERATIONS_RECOMMENDATION,
            "RECOMMENDED"
        )
        if leave_req.status not in valid_approval_statuses:
            raise ValidationError(f"Cannot approve leave in status '{leave_req.get_status_display()}'. Must be Awaiting Approval.")

        # If approved while awaiting Operations recommendation, record command bypass
        if leave_req.status == LeaveRequest.Status.PENDING_OPERATIONS_RECOMMENDATION:
            if not leave_req.operations_recommendation:
                leave_req.operations_recommendation = "BYPASS_BY_COMMAND"
                leave_req.operations_comments = (leave_req.operations_comments or "") + " [Operations recommendation bypassed by executive command decision]"

        # 3. Server-side Revalidation of dates & holidays
        date_calc = calculate_leave_dates(
            start_date=leave_req.start_date,
            duration=leave_req.duration,
            calculation_mode=leave_req.calculation_mode
        )
        leave_req.last_leave_date = date_calc['last_leave_date']
        leave_req.resume_date = date_calc['resume_date']
        leave_req.working_days_count = date_calc['working_days_count']
        leave_req.calendar_days_count = date_calc['calendar_days_count']

        # 4. Revalidate entitlement balance
        entitlement = leave_req.entitlement
        if leave_req.leave_type.code == "ANNUAL" and entitlement:
            entitlement = LeaveEntitlement.objects.select_for_update().get(pk=entitlement.pk)
            # Reserved days are already included in total_available; check total_available >= days_taken + duration
            if (entitlement.total_available - entitlement.days_taken) < leave_req.duration:
                raise ValidationError("Cannot approve: Insufficient leave balance remaining.")

            # Atomically convert reserved into taken
            entitlement.days_taken += leave_req.duration
            entitlement.days_reserved = max(0, entitlement.days_reserved - leave_req.duration)
            entitlement.save(update_fields=['days_taken', 'days_reserved', 'updated_at'])

        old_status = leave_req.status

        # Section 52 & 63: When Watch Room is established for the District, return to Administration
        # for Station Diary dispatch. When Watch Room is not established, command directly approves.
        has_watch_room = False
        if leave_req.routing_district:
            has_watch_room = FunctionalOffice.objects.filter(
                district=leave_req.routing_district,
                function_type=FunctionalOffice.FunctionType.WATCH_ROOM,
                is_established=True,
                active=True
            ).exists()

        if has_watch_room:
            leave_req.status = LeaveRequest.Status.PENDING_ADMIN_FINAL_PROCESSING
            if leave_req.routing_district:
                leave_req.processing_stage = LeaveRequest.ProcessingStage.DISTRICT_ADMIN
            elif leave_req.routing_region:
                leave_req.processing_stage = LeaveRequest.ProcessingStage.REGIONAL_ADMIN
            else:
                leave_req.processing_stage = LeaveRequest.ProcessingStage.DISTRICT_ADMIN
        else:
            today = timezone.localdate()
            if leave_req.start_date <= today <= leave_req.last_leave_date:
                leave_req.status = LeaveRequest.Status.ACTIVE
                NorminalRoll.objects.filter(pk=leave_req.personnel_id).update(
                    leave_status="ON LEAVE",
                    leave_type=leave_req.leave_type.name,
                    start_leave=leave_req.start_date,
                    end_leave=leave_req.last_leave_date
                )
            else:
                leave_req.status = LeaveRequest.Status.APPROVED
            leave_req.processing_stage = LeaveRequest.ProcessingStage.COMPLETED

        leave_req.approved_by = user
        leave_req.approved_at = timezone.now()
        leave_req.save()

        # Record decision history
        user_role = get_user_role(user)
        LeaveApprovalDecision.objects.create(
            leave_request=leave_req,
            approver=user,
            approver_role=user_role or "COMMANDING_OFFICER",
            decision="APPROVED",
            comments=comments or "Command approval granted. Awaiting final Administration processing.",
            previous_status=old_status,
            new_status=leave_req.status
        )

        LeaveWorkflowHistory.objects.create(
            leave_request=leave_req,
            action="COMMAND_APPROVED",
            from_status=old_status,
            to_status=leave_req.status,
            actor=user,
            actor_role="Command Approver",
            functional_office="Command",
            comment=comments or "Command approval granted."
        )

        # Audit
        AuditLog.objects.create(
            user=user,
            action="COMMAND_APPROVED",
            target_type="LeaveRequest",
            target_id=str(leave_req.id),
            description=f"Command approved leave request for {leave_req.personnel.fullname}.",
            region=leave_req.routing_region,
            district=leave_req.routing_district
        )

        # Notify applicant
        if leave_req.requested_by:
            send_notification(
                recipient=leave_req.requested_by,
                title="Leave Request Command Approved",
                message=f"Your {leave_req.leave_type.name} request for {leave_req.duration} days has been approved by Command. Awaiting Watch Room booking.",
                notification_type="SUCCESS",
                link_url=f"/my/leave/{leave_req.id}/"
            )

        return leave_req


def admin_release_to_watch_room(leave_req_id, user, comments=""):
    """
    Administration Final Processing: Dispatches approved leave to Watch Room (Section 52-54).
    Status becomes SENT_TO_WATCH_ROOM.
    """
    with transaction.atomic():
        leave_req = LeaveRequest.objects.select_for_update().select_related(
            'personnel', 'leave_type', 'routing_district', 'routing_region'
        ).get(pk=leave_req_id)

        if not can_user_release_to_watch_room(user, leave_req):
            raise PermissionDenied("You are not authorized to release leave to Watch Room.")

        if leave_req.status not in (LeaveRequest.Status.PENDING_ADMIN_FINAL_PROCESSING, LeaveRequest.Status.APPROVED):
            raise ValidationError(f"Cannot dispatch request in status '{leave_req.get_status_display()}' to Watch Room.")

        old_status = leave_req.status
        leave_req.status = LeaveRequest.Status.SENT_TO_WATCH_ROOM
        leave_req.processing_stage = LeaveRequest.ProcessingStage.WATCH_ROOM
        leave_req.sent_to_watch_room_by = user
        leave_req.sent_to_watch_room_at = timezone.now()
        leave_req.save(update_fields=['status', 'processing_stage', 'sent_to_watch_room_by', 'sent_to_watch_room_at', 'updated_at'])

        LeaveApprovalDecision.objects.create(
            leave_request=leave_req,
            approver=user,
            approver_role="ADMINISTRATION",
            decision="SENT_TO_WATCH_ROOM",
            comments=comments or "Approved leave dispatched to Watch Room for Station Diary entry.",
            previous_status=old_status,
            new_status=leave_req.status
        )

        LeaveWorkflowHistory.objects.create(
            leave_request=leave_req,
            action="SENT_TO_WATCH_ROOM",
            from_status=old_status,
            to_status=leave_req.status,
            actor=user,
            actor_role="Administration",
            functional_office="Administration",
            comment=comments or "Dispatched to Watch Room for operational booking."
        )

        # Audit
        AuditLog.objects.create(
            user=user,
            action="SENT_TO_WATCH_ROOM",
            target_type="LeaveRequest",
            target_id=str(leave_req.id),
            description=f"Administration dispatched approved leave for {leave_req.personnel.fullname} to Watch Room.",
            region=leave_req.routing_region,
            district=leave_req.routing_district
        )

        return leave_req


def watch_room_book_leave(leave_req_id, user, book_type="STATION_DIARY", book_reference="", remarks=""):
    """
    Watch Room Operational Booking (Section 40-43, 54, 83).
    Watch Room records the approved leave in the Station Diary / Watch Room Book.
    Cannot alter dates, duration, or approval decision.
    Sets status to BOOKED (or ACTIVE if today is within leave period).
    """
    with transaction.atomic():
        leave_req = LeaveRequest.objects.select_for_update().select_related(
            'personnel', 'leave_type', 'routing_district', 'routing_region'
        ).get(pk=leave_req_id)

        if not can_user_book_watch_room(user, leave_req):
            raise PermissionDenied("You are not authorized to book leave in the Watch Room.")

        if leave_req.status not in (
            LeaveRequest.Status.SENT_TO_WATCH_ROOM,
            LeaveRequest.Status.APPROVED,
            LeaveRequest.Status.PENDING_ADMIN_FINAL_PROCESSING
        ):
            raise ValidationError(f"Cannot book leave in status '{leave_req.get_status_display()}'. Must be sent to Watch Room.")

        # Check if already booked
        if leave_req.watch_room_booked_by:
            raise ValidationError("This leave request has already been booked in the Watch Room.")

        old_status = leave_req.status
        now = timezone.now()
        today = timezone.localdate()

        # Create WatchRoomBooking record (Section 42)
        booking = WatchRoomBooking.objects.create(
            leave_request=leave_req,
            district=leave_req.routing_district,
            booked_by=user,
            booked_at=now,
            book_type=book_type or "STATION_DIARY",
            book_reference=book_reference,
            remarks=remarks
        )

        leave_req.watch_room_booked_by = user
        leave_req.watch_room_booked_at = now
        leave_req.watch_room_book_type = book_type or "STATION_DIARY"
        leave_req.watch_room_book_reference = book_reference
        leave_req.watch_room_remarks = remarks

        # Determine if leave is currently active today
        is_currently_active = (leave_req.start_date <= today <= leave_req.last_leave_date)
        leave_req.status = LeaveRequest.Status.ACTIVE if is_currently_active else LeaveRequest.Status.BOOKED
        leave_req.processing_stage = LeaveRequest.ProcessingStage.COMPLETED
        leave_req.save()

        # Update NorminalRoll status if active
        personnel = leave_req.personnel
        if is_currently_active:
            personnel.leave_status = "ON LEAVE"
            personnel.start_leave = leave_req.start_date
            personnel.end_leave = leave_req.last_leave_date
            personnel.leave_type = leave_req.leave_type.name
            personnel.save(update_fields=['leave_status', 'start_leave', 'end_leave', 'leave_type'])

        LeaveApprovalDecision.objects.create(
            leave_request=leave_req,
            approver=user,
            approver_role="WATCH_ROOM",
            decision="WATCH_ROOM_BOOKED",
            comments=f"Booked in {book_type}. Ref: {book_reference}. Remarks: {remarks}",
            previous_status=old_status,
            new_status=leave_req.status
        )

        LeaveWorkflowHistory.objects.create(
            leave_request=leave_req,
            action="WATCH_ROOM_BOOKED",
            from_status=old_status,
            to_status=leave_req.status,
            actor=user,
            actor_role="Watch Room",
            functional_office="Watch Room",
            comment=f"Booked in {book_type}. Ref: {book_reference}."
        )

        # Audit
        AuditLog.objects.create(
            user=user,
            action="WATCH_ROOM_BOOKED",
            target_type="LeaveRequest",
            target_id=str(leave_req.id),
            description=f"Watch Room booked leave for {personnel.fullname} in {book_type} (Ref: {book_reference}).",
            region=leave_req.routing_region,
            district=leave_req.routing_district
        )

        # Notify applicant
        if leave_req.requested_by:
            send_notification(
                recipient=leave_req.requested_by,
                title="Leave Booking Complete",
                message=f"Your {leave_req.leave_type.name} has been booked in the Watch Room ({book_type} Ref: {book_reference}). Status: {leave_req.get_status_display()}.",
                notification_type="SUCCESS",
                link_url=f"/my/leave/{leave_req.id}/"
            )

        return leave_req


def final_reject_leave_request(leave_req_id, user, comments):
    """
    Executive Command Rejection (Section 50, 58).
    Requires explicit justification and preserves history.
    Releases reserved entitlement balance.
    Does NOT send to Watch Room (Section 58).
    """
    if not comments or not comments.strip():
        raise ValidationError("A justification is required when rejecting a leave request.")

    with transaction.atomic():
        leave_req = LeaveRequest.objects.select_for_update().select_related(
            'personnel', 'leave_type', 'entitlement', 'routing_district', 'routing_region'
        ).get(pk=leave_req_id)

        if not can_user_final_approve_leave(user, leave_req) and not can_user_process_leave(user, leave_req):
            raise PermissionDenied("You do not have jurisdiction to reject this leave request.")

        if leave_req.status not in (
            LeaveRequest.Status.PENDING_PROCESSING,
            LeaveRequest.Status.PENDING_ADMIN_REVIEW,
            LeaveRequest.Status.PENDING_OPERATIONS_RECOMMENDATION,
            LeaveRequest.Status.PENDING_APPROVAL
        ):
            raise ValidationError(f"Cannot reject request in status '{leave_req.get_status_display()}'.")

        old_status = leave_req.status

        # Release reserved entitlement
        entitlement = leave_req.entitlement
        if entitlement and leave_req.leave_type.code == "ANNUAL":
            entitlement = LeaveEntitlement.objects.select_for_update().get(pk=entitlement.pk)
            entitlement.days_reserved = max(0, entitlement.days_reserved - leave_req.duration)
            entitlement.save(update_fields=['days_reserved', 'updated_at'])

        leave_req.status = LeaveRequest.Status.REJECTED
        leave_req.processing_stage = LeaveRequest.ProcessingStage.COMPLETED
        leave_req.rejection_reason = comments
        leave_req.approved_by = user
        leave_req.approved_at = timezone.now()
        leave_req.save(update_fields=['status', 'processing_stage', 'rejection_reason', 'approved_by', 'approved_at', 'updated_at'])

        user_role = get_user_role(user)
        LeaveApprovalDecision.objects.create(
            leave_request=leave_req,
            approver=user,
            approver_role=user_role or "COMMANDING_OFFICER",
            decision="REJECTED",
            comments=comments,
            previous_status=old_status,
            new_status=leave_req.status
        )

        LeaveWorkflowHistory.objects.create(
            leave_request=leave_req,
            action="COMMAND_REJECTED",
            from_status=old_status,
            to_status=leave_req.status,
            actor=user,
            actor_role="Command Approver",
            functional_office="Command",
            comment=comments
        )

        # Audit
        AuditLog.objects.create(
            user=user,
            action="COMMAND_REJECTED",
            target_type="LeaveRequest",
            target_id=str(leave_req.id),
            description=f"Command rejected leave request for {leave_req.personnel.fullname}. Reason: {comments}",
            region=leave_req.routing_region,
            district=leave_req.routing_district
        )

        if leave_req.requested_by:
            send_notification(
                recipient=leave_req.requested_by,
                title="Leave Request Rejected",
                message=f"Your {leave_req.leave_type.name} request was rejected. Reason: {comments}",
                notification_type="WARNING",
                link_url=f"/my/leave/{leave_req.id}/"
            )

        return leave_req


def resubmit_corrected_leave_request(leave_req_id, user, start_date=None, duration=None,
                                     reason="", contact_phone="", contact_address="",
                                     handover_notes="", medical_reference="", attachment=None):
    """
    Allow an applicant to correct and resubmit a request in RETURNED_FOR_CORRECTION status.
    Moves request back to PENDING_ADMIN_REVIEW.
    """
    with transaction.atomic():
        leave_req = LeaveRequest.objects.select_for_update().select_related(
            'personnel', 'leave_type', 'entitlement'
        ).get(pk=leave_req_id)

        user_personnel = get_personnel_for_user(user)
        is_owner = (leave_req.requested_by_id == user.id) or (user_personnel and user_personnel.id == leave_req.personnel_id)
        if not is_owner and not user.is_superuser:
            raise PermissionDenied("You can only resubmit your own returned leave request.")

        if leave_req.status != LeaveRequest.Status.RETURNED_FOR_CORRECTION:
            raise ValidationError("Only requests in 'Returned for Correction' status can be resubmitted.")

        old_status = leave_req.status
        if start_date:
            leave_req.start_date = start_date
        if duration:
            leave_req.duration = duration

        # Re-calculate
        date_calc = calculate_leave_dates(
            start_date=leave_req.start_date,
            duration=leave_req.duration,
            calculation_mode=leave_req.calculation_mode
        )
        leave_req.last_leave_date = date_calc['last_leave_date']
        leave_req.resume_date = date_calc['resume_date']
        leave_req.working_days_count = date_calc['working_days_count']
        leave_req.calendar_days_count = date_calc['calendar_days_count']

        if reason:
            leave_req.reason = reason
        if contact_phone:
            leave_req.contact_phone = contact_phone
        if contact_address:
            leave_req.contact_address = contact_address
        if handover_notes:
            leave_req.handover_notes = handover_notes
        if medical_reference:
            leave_req.medical_reference = medical_reference
        if attachment:
            leave_req.attachment = attachment

        # Route
        route = resolve_leave_route(leave_req.personnel)
        leave_req.status = LeaveRequest.Status.PENDING_ADMIN_REVIEW
        leave_req.processing_stage = route['administrative_role']
        leave_req.routing_district = route['administrative_district']
        leave_req.routing_region = route['administrative_region']
        leave_req.correction_reason = ""
        leave_req.save()

        LeaveApprovalDecision.objects.create(
            leave_request=leave_req,
            approver=user,
            approver_role="APPLICANT",
            decision="RESUBMITTED",
            comments="Corrected and resubmitted for administrative processing.",
            previous_status=old_status,
            new_status=leave_req.status
        )

        LeaveWorkflowHistory.objects.create(
            leave_request=leave_req,
            action="RESUBMITTED",
            from_status=old_status,
            to_status=leave_req.status,
            actor=user,
            actor_role="Personnel",
            functional_office="Personnel",
            comment="Corrected and resubmitted by applicant."
        )

        return leave_req

