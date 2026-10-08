"""
Leave Recommendation Service for GNFS Operations.
Handles Operations functional review, recommendation logging, operational strength checks,
and routing directly to DFO/MFO command authority.
Crucial constraints:
- Operations performs RECOMMENDATION only, NEVER final approval.
- Recommendation does NOT deduct leave balance.
- Even NOT_RECOMMENDED decisions are preserved and forwarded to Command.
"""

import logging
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone

from gnfs_komenda.models import (
    LeaveRequest,
    LeaveApprovalDecision,
    LeaveWorkflowHistory,
    AuditLog,
)
from gnfs_komenda.security import get_user_role, send_notification
from gnfs_komenda.services.functional_scope_service import get_functional_scope

logger = logging.getLogger(__name__)


def can_user_recommend_leave(user, leave_req):
    """
    Determine if a user has authority to make an Operations recommendation on a leave request.
    Permitted: Superusers, District Operations officers (within district),
    or Regional Operations officers (within region).
    """
    if not user or not user.is_authenticated:
        return False

    if user.is_superuser:
        return True

    # Reject if user is the applicant
    if leave_req.requested_by_id == user.id:
        return False
    if leave_req.personnel and leave_req.personnel.staffID:
        if leave_req.personnel.staffID.strip().lower() == user.username.strip().lower():
            return False

    # Check Operations functional scope
    ops_scope = get_functional_scope(user, "OPERATIONS")
    if not ops_scope["authorized"]:
        return False

    level = ops_scope.get("level", "DISTRICT")
    if level == "NATIONAL":
        return True
    elif level == "REGIONAL":
        if leave_req.routing_region and ops_scope.get("region") == leave_req.routing_region:
            return True
        if leave_req.personnel and leave_req.personnel.region == ops_scope.get("region"):
            return True
    elif level == "DISTRICT":
        if leave_req.routing_district and ops_scope.get("district") == leave_req.routing_district:
            return True
        if leave_req.personnel and leave_req.personnel.station and leave_req.personnel.station.district == ops_scope.get("district"):
            return True

    return False


def operations_recommend_leave(leave_req_id, user, recommendation, comments=""):
    """
    Operations Recommendation Action (Prompt 3 Sections 7-10).
    Permitted decisions:
    - 'RECOMMEND': Operations recommends leave.
    - 'NOT_RECOMMEND': Operations does NOT recommend, but forwards to DFO/MFO with justification.
    - 'RETURN_FOR_CORRECTION': Operations returns request to applicant for correction.

    Guarantees:
    - Operations NEVER performs final approval.
    - Zero balance deduction.
    - Record recommendation, recommender, comments, and timestamp.
    - Set status to PENDING_DFO_MFO_DECISION on RECOMMEND / NOT_RECOMMEND.
    """
    rec_upper = recommendation.upper().strip()
    if rec_upper not in ("RECOMMEND", "NOT_RECOMMEND", "NOT_RECOMMENDED", "RETURN_FOR_CORRECTION"):
        raise ValidationError(f"Invalid recommendation action '{recommendation}'. Must be RECOMMEND, NOT_RECOMMEND, or RETURN_FOR_CORRECTION.")

    with transaction.atomic():
        leave_req = LeaveRequest.objects.select_for_update().select_related(
            'personnel', 'leave_type', 'entitlement', 'routing_district', 'routing_region'
        ).get(pk=leave_req_id)

        if not can_user_recommend_leave(user, leave_req):
            raise PermissionDenied("You do not have Operations authority to recommend this leave request.")

        # Guard: Operations cannot final approve
        if rec_upper in ("APPROVE", "FINAL_APPROVE"):
            raise PermissionDenied("Operations cannot perform final approval. Operations may only recommend or not recommend.")

        if leave_req.status not in (
            LeaveRequest.Status.PENDING_OPERATIONS_RECOMMENDATION,
            LeaveRequest.Status.PENDING_ADMIN_REVIEW,
        ):
            raise ValidationError(
                f"Request #{leave_req.id} is in status '{leave_req.get_status_display()}'; only requests pending Operations recommendation can be reviewed."
            )

        old_status = leave_req.status

        if rec_upper in ("RECOMMEND", "NOT_RECOMMEND", "NOT_RECOMMENDED"):
            is_recommended = (rec_upper == "RECOMMEND")
            rec_code = "RECOMMEND" if is_recommended else "NOT_RECOMMENDED"
            decision_val = "OPERATIONS_RECOMMENDED" if is_recommended else "OPERATIONS_NOT_RECOMMENDED"
            action_desc = "Operations Recommended" if is_recommended else "Operations Not Recommended"

            leave_req.operations_recommendation = rec_code
            leave_req.operations_recommended_by = user
            leave_req.operations_recommended_at = timezone.now()
            leave_req.operations_comments = comments

            # Advance to Command Decision
            leave_req.status = LeaveRequest.Status.PENDING_DFO_MFO_DECISION
            leave_req.processing_stage = LeaveRequest.ProcessingStage.COMMAND_APPROVAL
            leave_req.save(update_fields=[
                'operations_recommendation', 'operations_recommended_by',
                'operations_recommended_at', 'operations_comments',
                'status', 'processing_stage', 'updated_at'
            ])

            # Record formal decision
            LeaveApprovalDecision.objects.create(
                leave_request=leave_req,
                approver=user,
                approver_role="OPERATIONS",
                decision=decision_val,
                comments=comments or f"Operations recommendation recorded: {rec_code}",
                previous_status=old_status,
                new_status=leave_req.status
            )

            # Record workflow audit history
            LeaveWorkflowHistory.objects.create(
                leave_request=leave_req,
                action=decision_val,
                from_status=old_status,
                to_status=leave_req.status,
                actor=user,
                actor_role="District Operations Officer",
                functional_office="Operations",
                comment=comments or action_desc
            )

            # Audit log
            AuditLog.objects.create(
                user=user,
                action=decision_val,
                target_type="LeaveRequest",
                target_id=str(leave_req.id),
                description=f"{action_desc} for {leave_req.personnel.fullname} ({leave_req.personnel.staffID}). Forwarded to Command.",
                region=leave_req.routing_region,
                district=leave_req.routing_district,
                station=leave_req.personnel.station if leave_req.personnel else None,
            )

            # Notify command approver (DFO / MFO)
            if leave_req.routing_district:
                co_appt = leave_req.routing_district.current_commanding_officer
                if co_appt and co_appt.personnel:
                    from django.contrib.auth import get_user_model
                    UserModel = get_user_model()
                    user_co = UserModel.objects.filter(username__iexact=co_appt.personnel.staffID).first()
                    if user_co:
                        send_notification(
                            recipient=user_co,
                            title=f"Leave Awaiting Decision: {leave_req.personnel.fullname}",
                            message=f"Leave #{leave_req.reference_number or leave_req.id} ({action_desc}) is awaiting your command decision.",
                            notification_type="ACTION_REQUIRED",
                            link_url=f"/dashboard/district/{leave_req.routing_district.id}/command/"
                        )

        elif rec_upper == "RETURN_FOR_CORRECTION":
            leave_req.status = LeaveRequest.Status.RETURNED_FOR_CORRECTION
            leave_req.correction_reason = comments
            leave_req.operations_recommendation = "RETURNED_FOR_CORRECTION"
            leave_req.operations_recommended_by = user
            leave_req.operations_recommended_at = timezone.now()
            leave_req.operations_comments = comments
            leave_req.save(update_fields=[
                'status', 'correction_reason', 'operations_recommendation',
                'operations_recommended_by', 'operations_recommended_at',
                'operations_comments', 'updated_at'
            ])

            LeaveApprovalDecision.objects.create(
                leave_request=leave_req,
                approver=user,
                approver_role="OPERATIONS",
                decision="RETURNED_FOR_CORRECTION",
                comments=comments or "Returned for correction by Operations.",
                previous_status=old_status,
                new_status=leave_req.status
            )

            LeaveWorkflowHistory.objects.create(
                leave_request=leave_req,
                action="OPERATIONS_RETURNED_FOR_CORRECTION",
                from_status=old_status,
                to_status=leave_req.status,
                actor=user,
                actor_role="District Operations Officer",
                functional_office="Operations",
                comment=comments or "Returned for correction by Operations."
            )

            if leave_req.requested_by:
                send_notification(
                    recipient=leave_req.requested_by,
                    title="Leave Request Returned for Correction",
                    message=f"Your leave request #{leave_req.id} was returned by Operations: {comments}",
                    notification_type="WARNING",
                    link_url=f"/my/leave/{leave_req.id}/"
                )

        return leave_req
