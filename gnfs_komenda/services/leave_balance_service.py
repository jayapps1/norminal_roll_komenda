"""
Leave Balance and Entitlement Service for GNFS.
Implements:
- Senior (42 working days) vs Junior (36 working days) annual entitlement calculations.
- Proportionate / part-year entitlement calculations.
- Atomic reservation of leave balance upon request submission.
- Strictly SINGLE atomic balance deduction upon DFO/MFO command approval (select_for_update()).
- Release of reserved balance on rejection/cancellation.
- Restoration of days on recall.
"""

from decimal import Decimal
import logging
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from gnfs_komenda.models import LeaveEntitlement, LeaveType, NorminalRoll, Rank

logger = logging.getLogger(__name__)


def get_base_annual_entitlement(officer_category):
    """
    Returns base annual leave entitlement:
    - Senior Officer: 42 working days
    - Junior Officer: 36 working days
    """
    cat = str(officer_category or "").upper()
    if cat in ("SENIOR", "SENIOR_OFFICER", "SENIOR OFFICER"):
        return 42
    return 36


def calculate_proportionate_entitlement(personnel, effective_date=None, officer_category=None):
    """
    Calculate proportionate entitlement for officers appointed or promoted mid-year.
    Formula: (remaining months in year / 12) * annual entitlement.
    """
    if effective_date is None:
        effective_date = timezone.localdate()

    category = officer_category or getattr(personnel, 'officer_category', 'JUNIOR')
    annual_days = get_base_annual_entitlement(category)

    # Number of months remaining in the year inclusive of the effective month
    remaining_months = 12 - effective_date.month + 1
    proportionate_days = round((remaining_months / 12.0) * annual_days)
    return max(1, proportionate_days)


def get_or_create_leave_entitlement(personnel, leave_year, leave_type):
    """
    Retrieve or initialize the LeaveEntitlement record for the personnel, year, and leave type.
    """
    if not personnel or not leave_type:
        return None

    if leave_type.code != "ANNUAL":
        return None

    base_annual = get_base_annual_entitlement(personnel.officer_category)

    entitlement, created = LeaveEntitlement.objects.get_or_create(
        personnel=personnel,
        leave_year=leave_year,
        leave_type=leave_type,
        defaults={
            'base_entitlement': base_annual,
            'days_taken': 0,
            'days_reserved': 0,
            'days_carried_forward': 0,
        }
    )
    return entitlement


def reserve_leave_days(personnel, leave_type, leave_year, duration):
    """
    Atomically reserve days for a newly submitted leave request.
    Uses select_for_update() within transaction.atomic().
    """
    if leave_type.code != "ANNUAL":
        return None

    with transaction.atomic():
        entitlement = get_or_create_leave_entitlement(personnel, leave_year, leave_type)
        if not entitlement:
            return None

        # Lock the entitlement record
        locked_entitlement = LeaveEntitlement.objects.select_for_update().get(pk=entitlement.pk)

        available_balance = locked_entitlement.remaining_balance
        if available_balance < duration:
            raise ValidationError(
                f"Insufficient leave balance. Requested {duration} days, but only {available_balance} days available."
            )

        locked_entitlement.days_reserved += duration
        locked_entitlement.save(update_fields=['days_reserved', 'updated_at'])
        return locked_entitlement


def deduct_leave_balance_atomic(leave_request):
    """
    Strictly SINGLE atomic deduction of leave balance upon DFO/MFO command approval.
    Guarantees:
    - Recommendation does NOT deduct balance.
    - Administration review does NOT deduct balance.
    - Watch Room booking does NOT deduct balance.
    - Converts reserved days into taken days exactly once.
    """
    if not leave_request.leave_type or leave_request.leave_type.code != "ANNUAL":
        return None

    if not leave_request.entitlement:
        entitlement = get_or_create_leave_entitlement(
            leave_request.personnel,
            leave_request.leave_year,
            leave_request.leave_type
        )
        leave_request.entitlement = entitlement
        leave_request.save(update_fields=['entitlement'])

    entitlement_id = leave_request.entitlement_id
    if not entitlement_id:
        return None

    with transaction.atomic():
        locked_ent = LeaveEntitlement.objects.select_for_update().get(pk=entitlement_id)
        duration = leave_request.duration

        # Validate that total available (base + carried) >= days_taken + duration
        max_available = locked_ent.total_available
        if (max_available - locked_ent.days_taken) < duration:
            raise ValidationError(
                f"Cannot approve: Insufficient leave balance. Officer has {max_available - locked_ent.days_taken} days remaining, requested {duration}."
            )

        # Convert reserved into taken atomically
        locked_ent.days_taken += duration
        locked_ent.days_reserved = max(0, locked_ent.days_reserved - duration)
        locked_ent.save(update_fields=['days_taken', 'days_reserved', 'updated_at'])
        return locked_ent


def release_reserved_leave_days(leave_request):
    """
    Releases reserved days back to available balance when a request is rejected or cancelled.
    """
    if not leave_request.entitlement_id or leave_request.leave_type.code != "ANNUAL":
        return None

    with transaction.atomic():
        locked_ent = LeaveEntitlement.objects.select_for_update().get(pk=leave_request.entitlement_id)
        duration = leave_request.duration
        locked_ent.days_reserved = max(0, locked_ent.days_reserved - duration)
        locked_ent.save(update_fields=['days_reserved', 'updated_at'])
        return locked_ent


def restore_recalled_days(leave_request, days_to_restore):
    """
    Restores consumed leave days back to entitlement balance when an officer is recalled.
    """
    if not leave_request.entitlement_id or days_to_restore <= 0:
        return None

    with transaction.atomic():
        locked_ent = LeaveEntitlement.objects.select_for_update().get(pk=leave_request.entitlement_id)
        locked_ent.days_taken = max(0, locked_ent.days_taken - days_to_restore)
        locked_ent.save(update_fields=['days_taken', 'updated_at'])
        return locked_ent
