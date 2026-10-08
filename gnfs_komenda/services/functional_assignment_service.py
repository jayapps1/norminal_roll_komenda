"""
Enterprise Functional Assignment Service for GNFS Personnel.
Supports multiple concurrent functional assignments (Directorate -> Department -> Unit),
single-active primary assignment designation, safe assignment removal, and full audit logging.
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from gnfs_komenda.models import (
    PersonnelFunctionalAssignment,
    NorminalRoll,
    FunctionalUnit,
    AuditLog,
)


def _log_functional_audit(user, action, assignment, description, old_values=None, new_values=None):
    """Write an audit entry for functional assignment actions."""
    station = assignment.personnel.station if assignment.personnel else None
    district = station.district if station else None
    region = district.region if district else None

    AuditLog.objects.create(
        user=user if user and getattr(user, 'is_authenticated', False) else None,
        action=action,
        target_type="PersonnelFunctionalAssignment",
        target_id=str(assignment.id),
        description=description,
        old_values=old_values,
        new_values=new_values,
        region=region,
        district=district,
        station=station,
    )


def assign_functional_unit(personnel, functional_unit, assignment_role="Member",
                           is_primary=False, start_date=None, assigned_by=None,
                           approved_by=None, source_transfer=None, reason=""):
    """
    Assign an officer to a functional unit (Directorate, Department, or Unit).
    Supports multiple concurrent functional assignments.
    If is_primary=True, resets any prior primary flag on other assignments of this officer.
    """
    if not personnel or not functional_unit:
        raise ValidationError("Personnel and Functional Unit must both be specified.")

    if not functional_unit.active:
        raise ValidationError(f"Cannot assign to deactivated functional unit '{functional_unit.name}'.")

    if not start_date:
        start_date = timezone.now().date()

    with transaction.atomic():
        # 1. If designated as primary, clear primary from other active assignments
        if is_primary:
            PersonnelFunctionalAssignment.objects.filter(
                personnel=personnel,
                is_active=True,
                is_primary=True
            ).update(is_primary=False)

        # 2. Check if an active assignment for this specific unit already exists
        existing = PersonnelFunctionalAssignment.objects.filter(
            personnel=personnel,
            functional_unit=functional_unit,
            is_active=True
        ).first()

        if existing:
            existing.assignment_role = assignment_role or "Member"
            existing.is_primary = is_primary
            if reason:
                existing.reason = f"{existing.reason} | {reason}".strip(' |')
            existing.save(update_fields=['assignment_role', 'is_primary', 'reason'])
            assignment = existing
        else:
            assignment = PersonnelFunctionalAssignment.objects.create(
                personnel=personnel,
                functional_unit=functional_unit,
                assignment_role=assignment_role or "Member",
                is_primary=is_primary,
                is_active=True,
                is_current=True,
                start_date=start_date,
                reason=reason,
                source_transfer=source_transfer,
                assigned_by=assigned_by,
                approved_by=approved_by,
            )

        # 3. Synchronize cached primary functional_unit on NorminalRoll
        if is_primary or personnel.functional_unit_id is None:
            personnel.functional_unit = functional_unit
            personnel.save(update_fields=['functional_unit'])

        # 4. Write audit log
        pri_label = " [PRIMARY]" if is_primary else ""
        desc = (
            f"Assigned {personnel.fullname} to {functional_unit.get_full_path()} "
            f"as {assignment.assignment_role}{pri_label}."
        )
        _log_functional_audit(
            user=assigned_by or approved_by,
            action="ASSIGN_FUNCTIONAL_UNIT",
            assignment=assignment,
            description=desc,
            new_values={
                "unit": functional_unit.name,
                "role": assignment.assignment_role,
                "is_primary": is_primary,
                "start_date": str(start_date)
            }
        )

        return assignment


def remove_functional_assignment(assignment, end_date=None, user=None, reason=""):
    """
    Safely deactivate an officer's functional assignment.
    Retains history. If it was primary, falls back to another active assignment if one exists.
    """
    if not assignment:
        raise ValidationError("Assignment is required.")

    if not assignment.is_active:
        return assignment

    if not end_date:
        end_date = timezone.now().date()

    with transaction.atomic():
        was_primary = assignment.is_primary
        personnel = assignment.personnel

        assignment.is_active = False
        assignment.is_current = False
        assignment.is_primary = False
        assignment.end_date = end_date
        if reason:
            assignment.reason = f"{assignment.reason} | {reason}".strip(' |')
        assignment.save(update_fields=['is_active', 'is_current', 'is_primary', 'end_date', 'reason'])

        # If primary was removed, promote next active assignment or clear
        if was_primary:
            next_active = PersonnelFunctionalAssignment.objects.filter(
                personnel=personnel,
                is_active=True
            ).first()
            if next_active:
                next_active.is_primary = True
                next_active.save(update_fields=['is_primary'])
                personnel.functional_unit = next_active.functional_unit
            else:
                personnel.functional_unit = None
            personnel.save(update_fields=['functional_unit'])

        desc = (
            f"Removed {personnel.fullname} from {assignment.functional_unit.name}. "
            f"Reason: {reason or 'Assignment concluded'}"
        )
        _log_functional_audit(
            user=user,
            action="REMOVE_FUNCTIONAL_ASSIGNMENT",
            assignment=assignment,
            description=desc,
            old_values={"is_active": True, "was_primary": was_primary},
            new_values={"is_active": False, "end_date": str(end_date)}
        )

        return assignment


def set_primary_functional_assignment(personnel, assignment, user=None):
    """
    Designate a specific active assignment as the primary functional assignment.
    Atomically clears primary flag from all other active assignments.
    """
    if not personnel or not assignment:
        raise ValidationError("Personnel and Assignment must be provided.")

    if assignment.personnel_id != personnel.id:
        raise ValidationError("Assignment does not belong to this personnel.")

    if not assignment.is_active:
        raise ValidationError("Cannot designate an inactive assignment as primary.")

    with transaction.atomic():
        # Unset other primaries
        PersonnelFunctionalAssignment.objects.filter(
            personnel=personnel,
            is_active=True,
            is_primary=True
        ).exclude(id=assignment.id).update(is_primary=False)

        assignment.is_primary = True
        assignment.save(update_fields=['is_primary'])

        personnel.functional_unit = assignment.functional_unit
        personnel.save(update_fields=['functional_unit'])

        desc = f"Set {assignment.functional_unit.name} as primary functional assignment for {personnel.fullname}."
        _log_functional_audit(
            user=user,
            action="SET_PRIMARY_FUNCTIONAL_ASSIGNMENT",
            assignment=assignment,
            description=desc,
            new_values={"functional_unit": assignment.functional_unit.name, "is_primary": True}
        )

        return assignment


def get_active_functional_assignments(personnel):
    """
    Return QuerySet of active functional assignments for an officer, primary first.
    """
    return PersonnelFunctionalAssignment.objects.filter(
        personnel=personnel,
        is_active=True
    ).select_related('functional_unit', 'functional_unit__parent').order_by('-is_primary', 'functional_unit__name')


def get_officer_functional_history(personnel):
    """
    Return full chronological functional placement history for an officer.
    """
    return PersonnelFunctionalAssignment.objects.filter(
        personnel=personnel
    ).select_related('functional_unit', 'functional_unit__parent', 'assigned_by').order_by('-start_date', '-created_at')


# --- Backward compatibility wrappers ---

def close_active_functional_assignment(personnel, end_date=None, reason=""):
    """
    Backward-compatible wrapper to close all active functional assignments for an officer.
    """
    if not end_date:
        end_date = timezone.now().date()

    assignments = PersonnelFunctionalAssignment.objects.filter(
        personnel=personnel,
        is_active=True
    )
    count = 0
    for a in assignments:
        remove_functional_assignment(a, end_date=end_date, reason=reason)
        count += 1
    return count


def assign_officer_functional_unit(personnel, functional_unit, start_date=None,
                                   assigned_by=None, approved_by=None,
                                   source_transfer=None, reason="", is_primary=True):
    """
    Backward-compatible wrapper for single assignment placement, defaults to is_primary=True.
    """
    return assign_functional_unit(
        personnel=personnel,
        functional_unit=functional_unit,
        assignment_role="Member",
        is_primary=is_primary,
        start_date=start_date,
        assigned_by=assigned_by,
        approved_by=approved_by,
        source_transfer=source_transfer,
        reason=reason
    )
