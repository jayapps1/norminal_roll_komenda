"""
Leave Deferment & Carry-Forward Service for GNFS.
Only approved deferments generate carry-forward into subsequent leave years.
"""

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone

from gnfs_komenda.models import LeaveDefermentRequest, LeaveEntitlement, LeaveType
from gnfs_komenda.security import get_user_role, get_user_assignment
from gnfs_komenda.workflow import record_audit_log


def can_user_approve_deferment(user, deferment_request):
    """
    Check if user has jurisdiction to approve a deferment.
    Enforces anti-self-approval.
    """
    if not user or not user.is_authenticated:
        return False

    personnel = deferment_request.personnel
    if not personnel:
        return False

    # Anti-self-approval
    if user.id == deferment_request.personnel_id:
        return False
    if personnel.staffID and personnel.staffID.strip().lower() == user.username.strip().lower():
        return False
    personnel_email = getattr(personnel, 'email', None)
    if personnel_email and user.email and str(personnel_email).strip().lower() == user.email.strip().lower():
        return False

    if user.is_superuser:
        return True

    role = get_user_role(user)
    if role == "NATIONAL_ADMIN":
        return True

    assignment = get_user_assignment(user)
    if not assignment or not assignment.active:
        return False

    station = personnel.station
    if not station:
        return False

    district = station.district
    region = district.region if district else None

    if role == "REGIONAL_ADMIN":
        return region and assignment.region_id == region.id

    if role in ("DFO", "DISTRICT_ADMIN"):
        return district and assignment.district_id == district.id

    return False


def request_leave_deferment(personnel, leave_year, days_requested, reason="", user=None):
    """
    Create a new LeaveDefermentRequest.
    """
    annual_type = LeaveType.objects.filter(code="ANNUAL").first()
    if not annual_type:
        raise ValidationError("Annual leave type is not configured in the system.")

    entitlement = LeaveEntitlement.objects.filter(
        personnel=personnel,
        leave_year=leave_year,
        leave_type=annual_type
    ).first()

    available_balance = entitlement.remaining_balance if entitlement else 0
    if days_requested > available_balance:
        raise ValidationError(
            f"Cannot defer {days_requested} days. Only {available_balance} remaining days available."
        )

    if days_requested <= 0:
        raise ValidationError("Days requested for deferment must be greater than zero.")

    deferment = LeaveDefermentRequest.objects.create(
        personnel=personnel,
        leave_year=leave_year,
        carried_into_year=leave_year + 1,
        days_requested=days_requested,
        reason=reason,
        requested_by=user,
        status=LeaveDefermentRequest.Status.PENDING
    )

    record_audit_log(
        user=user,
        action="REQUEST_DEFERMENT",
        target_type="LEAVE_DEFERMENT",
        target_id=deferment.id,
        description=f"Requested deferment of {days_requested} days from {leave_year} to {leave_year + 1} for {personnel.staffID}",
        station=personnel.station,
        post=personnel.post
    )

    return deferment


def approve_leave_deferment(deferment_id, user, approved_days, remarks=""):
    """
    Approve deferment and carry forward approved days into target year's entitlement.
    """
    with transaction.atomic():
        deferment = LeaveDefermentRequest.objects.select_for_update().select_related(
            'personnel'
        ).get(pk=deferment_id)

        if not can_user_approve_deferment(user, deferment):
            raise PermissionDenied(
                "You do not have permission to approve this deferment or are attempting self-approval."
            )

        if deferment.status != LeaveDefermentRequest.Status.PENDING:
            raise ValidationError(f"Cannot approve deferment with status '{deferment.get_status_display()}'.")

        if approved_days <= 0 or approved_days > deferment.days_requested:
            raise ValidationError(
                f"Approved days must be between 1 and requested days ({deferment.days_requested})."
            )

        deferment.status = LeaveDefermentRequest.Status.APPROVED
        deferment.approved_days = approved_days
        deferment.approved_by = user
        deferment.approved_at = timezone.now()
        deferment.remarks = remarks

        personnel = deferment.personnel
        target_year = deferment.target_year or (deferment.leave_year + 1)
        leave_type = LeaveType.objects.get(code="ANNUAL")

        # Get or create target year entitlement
        target_ent, created = LeaveEntitlement.objects.select_for_update().get_or_create(
            personnel=personnel,
            leave_year=target_year,
            leave_type=leave_type,
            defaults={
                'base_entitlement': 42 if getattr(personnel, 'officer_category', None) == "SENIOR" else 36
            }
        )

        target_ent.carried_forward += approved_days
        target_ent.save(update_fields=['carried_forward'])

        deferment.is_carried_forward = True
        deferment.save()

        record_audit_log(
            user=user,
            action="APPROVE_DEFERMENT",
            target_type="LEAVE_DEFERMENT",
            target_id=deferment.id,
            description=f"Approved deferment #{deferment.id}: {approved_days} days carried forward to {target_year} for {personnel.staffID}",
            station=personnel.station,
            post=personnel.post
        )

    return deferment


def reject_leave_deferment(deferment_id, user, remarks=""):
    """
    Reject deferment request. Days remain in original year and will expire at year end.
    """
    with transaction.atomic():
        deferment = LeaveDefermentRequest.objects.select_for_update().select_related('personnel').get(pk=deferment_id)

        if not can_user_approve_deferment(user, deferment):
            raise PermissionDenied(
                "You do not have permission to reject this deferment or are attempting self-action."
            )

        if deferment.status != LeaveDefermentRequest.Status.PENDING:
            raise ValidationError(f"Cannot reject deferment with status '{deferment.get_status_display()}'.")

        deferment.status = LeaveDefermentRequest.Status.REJECTED
        deferment.approved_by = user
        deferment.approved_at = timezone.now()
        deferment.approval_remarks = remarks
        deferment.save()

        record_audit_log(
            user=user,
            action="REJECT_DEFERMENT",
            target_type="LEAVE_DEFERMENT",
            target_id=deferment.id,
            description=f"Rejected deferment #{deferment.id} for {deferment.personnel.staffID}: {remarks}",
            station=deferment.personnel.station,
            post=deferment.personnel.post
        )

    return deferment
