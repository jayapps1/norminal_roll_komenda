"""
Leave Recall & Call-Off Service for GNFS.
Handles officer recall from active leave, calculates consumed vs unused days,
restores unused days back to LeaveEntitlement balance, and marks NorminalRoll as called off / AT POST.
"""

from datetime import timedelta
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone

from gnfs_komenda.models import LeaveRequest, LeaveType, LeaveEntitlement
from gnfs_komenda.services.leave_approval_service import can_user_approve_leave
from gnfs_komenda.services.leave_calculator import is_working_day, get_public_holidays_set
from gnfs_komenda.workflow import record_audit_log


def recall_officer_from_leave(leave_request_id, user, recall_date, recall_reason=""):
    """
    Recall officer from approved or active leave.
    Calculates actual days consumed and restores remainder to balance.
    """
    with transaction.atomic():
        leave_req = LeaveRequest.objects.select_for_update().select_related(
            'personnel', 'entitlement', 'leave_type'
        ).get(pk=leave_request_id)

        if not can_user_approve_leave(user, leave_req):
            raise PermissionDenied("You do not have jurisdiction to recall this officer.")

        if leave_req.status not in (LeaveRequest.Status.APPROVED, LeaveRequest.Status.ACTIVE):
            raise ValidationError(
                f"Cannot recall leave with status '{leave_req.get_status_display()}'. "
                "Only APPROVED or ACTIVE leaves can be recalled."
            )

        if recall_date < leave_req.start_date:
            raise ValidationError(
                f"Recall date ({recall_date}) cannot be earlier than leave start date ({leave_req.start_date})."
            )

        if recall_date > leave_req.last_leave_date:
            raise ValidationError(
                f"Recall date ({recall_date}) is after the leave end date ({leave_req.last_leave_date})."
            )

        # Calculate actual days consumed up to recall_date - 1 day
        original_days = (
            leave_req.working_days_count
            if leave_req.calculation_mode == LeaveType.CalculationMode.WORKING_DAYS
            else leave_req.duration
        )

        if recall_date == leave_req.start_date:
            days_consumed = 0
        else:
            effective_end = recall_date - timedelta(days=1)
            if leave_req.calculation_mode == LeaveType.CalculationMode.WORKING_DAYS:
                holiday_set = get_public_holidays_set(leave_req.start_date.year, recall_date.year)
                cur = leave_req.start_date
                days_consumed = 0
                while cur <= effective_end:
                    if is_working_day(cur, holiday_set):
                        days_consumed += 1
                    cur += timedelta(days=1)
            else:
                days_consumed = (effective_end - leave_req.start_date).days + 1

        days_restored = max(0, original_days - days_consumed)

        leave_req.is_recalled = True
        leave_req.recalled_by = user
        leave_req.recalled_at = timezone.now()
        leave_req.recall_date = recall_date
        leave_req.recall_reason = recall_reason
        leave_req.actual_days_consumed = days_consumed
        leave_req.days_restored = days_restored
        leave_req.status = LeaveRequest.Status.RECALLED
        leave_req.save()

        # Restore unused days to entitlement
        if leave_req.entitlement and days_restored > 0:
            ent = LeaveEntitlement.objects.select_for_update().get(pk=leave_req.entitlement_id)
            ent.days_taken = max(0, ent.days_taken - days_restored)
            ent.save(update_fields=['days_taken'])

        # Update NorminalRoll operational fields
        personnel = leave_req.personnel
        if personnel:
            personnel.called_off = True
            personnel.leave_status = "AT POST"
            personnel.save(update_fields=['called_off', 'leave_status'])

        record_audit_log(
            user=user,
            action="RECALL_LEAVE",
            target_type="LEAVE_REQUEST",
            target_id=leave_req.id,
            description=(
                f"Recalled {personnel.staffID} from leave on {recall_date}. "
                f"Consumed: {days_consumed} days, Restored to balance: {days_restored} days. Reason: {recall_reason}"
            ),
            station=personnel.station,
            post=personnel.post
        )

    return leave_req
