"""
Organisational Structure Service for GNFS Functional Units.
Provides full CRUD management for Directorates, Departments/Divisions, and Units.
Enforces safe deactivation (preventing hard deletion of historically referenced units),
validates cascading parent-child hierarchies, and generates nested organisational trees.
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from gnfs_komenda.models import (
    FunctionalUnit,
    PersonnelFunctionalAssignment,
    NorminalRoll,
    FunctionalRoleAssignment,
    AuditLog,
)


def _log_org_audit(user, action, unit, description, old_values=None, new_values=None):
    """Internal helper to write audit entries for organisational structure mutations."""
    AuditLog.objects.create(
        user=user if user and getattr(user, 'is_authenticated', False) else None,
        action=action,
        target_type="FunctionalUnit",
        target_id=str(unit.id),
        description=description,
        old_values=old_values,
        new_values=new_values,
    )


def validate_unit_hierarchy(unit_type, parent):
    """
    Validate that parent is appropriate for the given unit_type:
    - DIRECTORATE: must have parent=None
    - DIVISION / DEPARTMENT: parent must be DIRECTORATE
    - UNIT: parent must be DEPARTMENT or DIVISION
    - SECTION: parent must be UNIT
    """
    if unit_type == FunctionalUnit.FunctionalUnitType.DIRECTORATE:
        if parent is not None:
            raise ValidationError("A Directorate is a top-level organisational unit and cannot have a parent.")
    elif unit_type in (FunctionalUnit.FunctionalUnitType.DIVISION, FunctionalUnit.FunctionalUnitType.DEPARTMENT):
        if not parent or parent.unit_type != FunctionalUnit.FunctionalUnitType.DIRECTORATE:
            raise ValidationError("A Department or Division must have a Directorate as its parent.")
    elif unit_type == FunctionalUnit.FunctionalUnitType.UNIT:
        if not parent or parent.unit_type not in (
            FunctionalUnit.FunctionalUnitType.DEPARTMENT,
            FunctionalUnit.FunctionalUnitType.DIVISION,
            FunctionalUnit.FunctionalUnitType.DIRECTORATE,
        ):
            raise ValidationError("A Unit must belong to a Department, Division, or Directorate.")
    elif unit_type == FunctionalUnit.FunctionalUnitType.SECTION:
        if not parent or parent.unit_type != FunctionalUnit.FunctionalUnitType.UNIT:
            raise ValidationError("A Section must belong to a Unit.")


def create_functional_unit(name, unit_type, parent=None, code="", description="",
                           display_order=0, active=True, user=None):
    """
    Create a new FunctionalUnit with validated hierarchy.
    """
    name = (name or "").strip()
    code = (code or "").strip()
    if not name:
        raise ValidationError("Unit name cannot be blank.")

    validate_unit_hierarchy(unit_type, parent)

    # Check unique constraint
    if parent:
        if FunctionalUnit.objects.filter(parent=parent, name__iexact=name).exists():
            raise ValidationError(f"A unit named '{name}' already exists under '{parent.name}'.")
    else:
        if FunctionalUnit.objects.filter(parent__isnull=True, name__iexact=name).exists():
            raise ValidationError(f"A top-level unit named '{name}' already exists.")

    unit = FunctionalUnit.objects.create(
        name=name,
        code=code,
        unit_type=unit_type,
        parent=parent,
        description=description,
        display_order=display_order,
        active=active,
    )

    desc = f"Created {unit.get_unit_type_display()} '{unit.name}' (Code: {unit.code or 'None'})."
    _log_org_audit(
        user=user,
        action="CREATE_FUNCTIONAL_UNIT",
        unit=unit,
        description=desc,
        new_values={"name": unit.name, "type": unit.unit_type, "code": unit.code, "active": unit.active}
    )

    return unit


def update_functional_unit(unit, name=None, code=None, description=None,
                           display_order=None, active=None, parent=None, user=None):
    """
    Update an existing FunctionalUnit.
    """
    old_values = {
        "name": unit.name,
        "code": unit.code,
        "description": unit.description,
        "display_order": unit.display_order,
        "active": unit.active,
        "parent_id": unit.parent_id,
    }

    if parent is not None:
        new_parent = parent
        validate_unit_hierarchy(unit.unit_type, new_parent)
        # Prevent self-parenting or circular parenting
        if new_parent and new_parent.id == unit.id:
            raise ValidationError("A unit cannot be its own parent.")
        if new_parent and new_parent.id in unit.get_descendant_ids(include_self=True):
            raise ValidationError("Circular hierarchy: parent cannot be a descendant of this unit.")
        unit.parent = new_parent

    if name is not None:
        name = name.strip()
        if not name:
            raise ValidationError("Unit name cannot be blank.")
        # Check duplicate
        if unit.parent:
            duplicate = FunctionalUnit.objects.filter(parent=unit.parent, name__iexact=name).exclude(id=unit.id).exists()
        else:
            duplicate = FunctionalUnit.objects.filter(parent__isnull=True, name__iexact=name).exclude(id=unit.id).exists()
        if duplicate:
            raise ValidationError(f"Another unit named '{name}' already exists at this hierarchy level.")
        unit.name = name

    if code is not None:
        unit.code = code.strip()
    if description is not None:
        unit.description = description
    if display_order is not None:
        unit.display_order = display_order
    if active is not None:
        unit.active = active

    unit.save()

    desc = f"Updated {unit.get_unit_type_display()} '{unit.name}'."
    _log_org_audit(
        user=user,
        action="UPDATE_FUNCTIONAL_UNIT",
        unit=unit,
        description=desc,
        old_values=old_values,
        new_values={
            "name": unit.name,
            "code": unit.code,
            "display_order": unit.display_order,
            "active": unit.active,
            "parent_id": unit.parent_id,
        }
    )

    return unit


def deactivate_functional_unit(unit, user=None, deactivate_children=True):
    """
    Safely deactivate a FunctionalUnit and optionally its children.
    Preserves all historical assignments and references.
    """
    if not unit.active:
        return unit

    with transaction.atomic():
        unit.active = False
        unit.save(update_fields=['active'])

        if deactivate_children:
            descendant_ids = unit.get_descendant_ids(include_self=False)
            FunctionalUnit.objects.filter(id__in=descendant_ids).update(active=False)

        desc = f"Deactivated {unit.get_unit_type_display()} '{unit.name}'."
        _log_org_audit(
            user=user,
            action="DEACTIVATE_FUNCTIONAL_UNIT",
            unit=unit,
            description=desc,
            old_values={"active": True},
            new_values={"active": False}
        )

        return unit


def reactivate_functional_unit(unit, user=None):
    """
    Reactivate a FunctionalUnit. Enforces that its parent (if any) must also be active.
    """
    if unit.parent and not unit.parent.active:
        raise ValidationError(f"Cannot reactivate '{unit.name}': Parent unit '{unit.parent.name}' is inactive. Reactivate the parent first.")

    unit.active = True
    unit.save(update_fields=['active'])

    desc = f"Reactivated {unit.get_unit_type_display()} '{unit.name}'."
    _log_org_audit(
        user=user,
        action="REACTIVATE_FUNCTIONAL_UNIT",
        unit=unit,
        description=desc,
        old_values={"active": False},
        new_values={"active": True}
    )

    return unit


def is_unit_referenced_historically(unit):
    """
    Check if the functional unit or any of its descendants has historical or active references
    in PersonnelFunctionalAssignment, NorminalRoll, or FunctionalRoleAssignment.
    """
    unit_ids = unit.get_descendant_ids(include_self=True)

    if PersonnelFunctionalAssignment.objects.filter(functional_unit_id__in=unit_ids).exists():
        return True, "Personnel have historical or active functional assignments in this unit."

    if NorminalRoll.objects.filter(functional_unit_id__in=unit_ids).exists():
        return True, "Personnel records reference this functional unit."

    if FunctionalRoleAssignment.objects.filter(organisational_unit_id__in=unit_ids).exists():
        return True, "Management or leadership roles are associated with this unit."

    return False, ""


def delete_functional_unit(unit, user=None):
    """
    Attempt to hard delete a FunctionalUnit.
    Strictly PREVENTS deletion if referenced historically or has child units.
    Directs the administrator to deactivate instead.
    """
    # 1. Check for child units
    if unit.children.exists():
        raise ValidationError(
            f"Cannot delete '{unit.name}': It has {unit.children.count()} child units. "
            f"Deactivate the unit instead or delete child units first."
        )

    # 2. Check for historical or active references
    is_ref, reason = is_unit_referenced_historically(unit)
    if is_ref:
        raise ValidationError(
            f"Cannot delete '{unit.name}': {reason} "
            f"Organisational records with history must be deactivated, not deleted, to preserve audit integrity."
        )

    unit_name = unit.name
    unit_type_name = unit.get_unit_type_display()
    unit_id = unit.id

    unit.delete()

    desc = f"Deleted unreferenced {unit_type_name} '{unit_name}' (ID: {unit_id})."
    AuditLog.objects.create(
        user=user if user and getattr(user, 'is_authenticated', False) else None,
        action="DELETE_FUNCTIONAL_UNIT",
        target_type="FunctionalUnit",
        target_id=str(unit_id),
        description=desc,
    )
    return True


def get_organisation_tree():
    """
    Return a structured nested hierarchy of Directorates -> Departments -> Units.
    Each node includes: id, name, code, unit_type, active, display_order, personnel_count, children.
    """
    directorates = FunctionalUnit.objects.filter(
        unit_type=FunctionalUnit.FunctionalUnitType.DIRECTORATE
    ).order_by('display_order', 'name')

    tree = []
    for dir_obj in directorates:
        departments = FunctionalUnit.objects.filter(
            parent=dir_obj
        ).order_by('display_order', 'name')

        dept_list = []
        dir_personnel_count = PersonnelFunctionalAssignment.objects.filter(
            functional_unit_id__in=dir_obj.get_descendant_ids(include_self=True),
            is_active=True
        ).values('personnel_id').distinct().count()

        for dept_obj in departments:
            units = FunctionalUnit.objects.filter(
                parent=dept_obj
            ).order_by('display_order', 'name')

            unit_list = []
            dept_personnel_count = PersonnelFunctionalAssignment.objects.filter(
                functional_unit_id__in=dept_obj.get_descendant_ids(include_self=True),
                is_active=True
            ).values('personnel_id').distinct().count()

            for unit_obj in units:
                unit_personnel_count = PersonnelFunctionalAssignment.objects.filter(
                    functional_unit_id__in=unit_obj.get_descendant_ids(include_self=True),
                    is_active=True
                ).values('personnel_id').distinct().count()

                unit_list.append({
                    "id": unit_obj.id,
                    "name": unit_obj.name,
                    "code": unit_obj.code,
                    "unit_type": unit_obj.unit_type,
                    "unit_type_display": unit_obj.get_unit_type_display(),
                    "active": unit_obj.active,
                    "personnel_count": unit_personnel_count,
                    "children": []
                })

            dept_list.append({
                "id": dept_obj.id,
                "name": dept_obj.name,
                "code": dept_obj.code,
                "unit_type": dept_obj.unit_type,
                "unit_type_display": dept_obj.get_unit_type_display(),
                "active": dept_obj.active,
                "personnel_count": dept_personnel_count,
                "children": unit_list,
            })

        tree.append({
            "id": dir_obj.id,
            "name": dir_obj.name,
            "code": dir_obj.code,
            "unit_type": dir_obj.unit_type,
            "unit_type_display": dir_obj.get_unit_type_display(),
            "active": dir_obj.active,
            "personnel_count": dir_personnel_count,
            "children": dept_list,
        })

    return tree
