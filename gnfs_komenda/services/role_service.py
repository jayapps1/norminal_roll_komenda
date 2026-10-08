"""
Role-Based Access Control (RBAC) & Administrative Assignment Service for GNFS.
Implements Section 8-18, 39-51, 65-70 of the GNFS National Specification.
Enforces Backend Security Invariants:
1. NO SELF-DEMOTION
2. NO SELF-PROMOTION
3. NO CROSS-SCOPE ACCESS
4. NO LOWER-LEVEL MODIFICATION OF HIGHER ADMIN
5. NO ORDINARY USER MODIFICATION OF SUPER ADMIN
6. ONE OFFICER = ONE USER ACCOUNT
"""

import logging
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from gnfs_komenda.models import (
    ApplicationRole, AppPermission, RolePermission, AdministrativeAssignment,
    NorminalRoll, Region, District, AuditLog, Notification,
    FunctionalOffice, FunctionalUnit, FunctionalRoleAssignment, FunctionalRoleType,
    PersonnelPosting
)
from staff.models import UserAssignment, GNFSUserRole

logger = logging.getLogger(__name__)


STANDARD_PERMISSIONS = [
    # PERSONNEL
    ("personnel.view", "View Personnel", "View personnel records within authorized scope", "PERSONNEL"),
    ("personnel.create", "Enroll Personnel", "Create new personnel records within authorized scope", "PERSONNEL"),
    ("personnel.edit", "Edit Personnel", "Update personnel records within authorized scope", "PERSONNEL"),
    ("personnel.archive", "Archive Personnel", "Archive or deactivate personnel records within authorized scope", "PERSONNEL"),

    # COMMAND STRUCTURE
    ("region.manage", "Manage Regions", "Configure regional commands and settings", "COMMAND_STRUCTURE"),
    ("district.manage", "Manage Districts", "Configure district commands and settings", "COMMAND_STRUCTURE"),
    ("station.manage", "Manage Stations", "Configure fire stations and sub-stations", "COMMAND_STRUCTURE"),
    ("post.manage", "Manage Posts", "Configure fire posts", "COMMAND_STRUCTURE"),

    # ADMINISTRATION
    ("admin.assign_regional", "Assign Regional Admin", "Assign Regional Administrator authority", "ADMINISTRATION"),
    ("admin.revoke_regional", "Revoke Regional Admin", "Revoke Regional Administrator authority", "ADMINISTRATION"),
    ("admin.assign_district", "Assign District Admin", "Assign District Administrator authority", "ADMINISTRATION"),
    ("admin.revoke_district", "Revoke District Admin", "Revoke District Administrator authority", "ADMINISTRATION"),

    # ROLES & PERMISSIONS
    ("role.view", "View Roles", "View application roles and assignments", "ROLES_PERMISSIONS"),
    ("role.create", "Create Roles", "Create new application roles", "ROLES_PERMISSIONS"),
    ("role.edit", "Edit Roles", "Update application roles and permissions", "ROLES_PERMISSIONS"),
    ("role.assign", "Assign Roles", "Assign administrative roles to officers", "ROLES_PERMISSIONS"),
    ("role.revoke", "Revoke Roles", "Revoke administrative roles from officers", "ROLES_PERMISSIONS"),
    ("permission.manage", "Manage Permissions", "Configure role permissions matrix", "ROLES_PERMISSIONS"),

    # COMMAND APPOINTMENTS
    ("appointment.manage_regional", "Manage Regional Leadership", "Appoint or end RFO / Regional 2IC appointments", "COMMAND_APPOINTMENTS"),
    ("appointment.manage_district", "Manage District Leadership", "Appoint or end DFO / MFO / District 2IC appointments", "COMMAND_APPOINTMENTS"),

    # LEAVE
    ("leave.view", "View Leave", "View leave applications and records within authorized scope", "LEAVE"),
    ("leave.process", "Process Leave", "Review and process leave applications administratively", "LEAVE"),
    ("leave.approve", "Approve Leave", "Final command decision (Approve/Reject) on leave applications", "LEAVE"),

    # TRANSFERS
    ("transfer.view", "View Transfers", "View transfer records within authorized scope", "TRANSFERS"),
    ("transfer.process", "Process Transfers", "Process transfer release, arrival, and placement", "TRANSFERS"),
    ("transfer.approve", "Approve Transfers", "Approve personnel transfers", "TRANSFERS"),

    # ORGANISATION
    ("organisation.manage", "Manage Organisation", "Manage functional directorates, departments, and units", "ORGANISATION"),
    ("functional.assign", "Assign Functional Roles", "Assign officers to functional directorates/departments/units", "ORGANISATION"),

    # REPORTS & AUDIT
    ("report.view", "View Reports", "View executive and operational reports within authorized scope", "REPORTS_AUDIT"),
    ("audit.view", "View Audit Logs", "View system security and administrative audit logs", "REPORTS_AUDIT"),

    # NOMINAL ROLL & ACCOUNTS (Section 11-18, 94)
    ("nominal_roll.view", "View Nominal Roll", "Access functional Nominal Roll workspace", "ACCOUNTS"),
    ("nominal_roll.export", "Export Nominal Roll", "Export Nominal Roll data within authorized scope", "ACCOUNTS"),
    ("accounts.nominal_roll_view", "Accounts Nominal Roll View", "View Nominal Roll in Accounts workspace", "ACCOUNTS"),

    # FUNCTIONAL ADMINISTRATION (Section 19-28, 94)
    ("administration.personnel_view", "Administration Personnel View", "View personnel administrative records", "ADMINISTRATION"),
    ("administration.leave_view", "Administration Leave View", "View leave records in Administration workspace", "ADMINISTRATION"),
    ("administration.leave_process", "Process Leave Administration", "Review and process leave applications administratively", "ADMINISTRATION"),
    ("administration.leave_forward", "Forward Leave to Operations", "Forward leave request to Operations for recommendation", "ADMINISTRATION"),

    # FUNCTIONAL OPERATIONS (Section 29-35, 94)
    ("operations.personnel_view", "Operations Personnel View", "View operational personnel availability and strength", "OPERATIONS"),
    ("operations.leave_recommend", "Operations Leave Recommendation", "Submit Operations recommendation on leave requests", "OPERATIONS"),

    # COMMAND LEAVE DECISION (Section 50-51, 94)
    ("command.leave_decide", "Command Leave Decision", "DFO / MFO / RFO command approval or rejection of leave", "COMMAND_APPOINTMENTS"),

    # WATCH ROOM (Section 40-43, 94)
    ("watch_room.leave_view", "Watch Room Leave View", "View approved leave sent to Watch Room", "WATCH_ROOM"),
    ("watch_room.leave_book", "Watch Room Book Leave", "Record approved leave into official station diary", "WATCH_ROOM"),

    # HUMAN RESOURCE / HR (Section 36-39, 94)
    ("hr.personnel_view", "HR Personnel View", "View personnel and service information in HR workspace", "HR"),
    ("functional.manage_assignment", "Manage Functional Assignments", "Assign officers to functional offices and management roles", "ORGANISATION"),
]


def seed_roles_and_permissions(creator=None):
    """
    Ensure all standard application roles and permissions exist in the database.
    Configures default permission sets for standard system roles.
    """
    perm_objs = {}
    for code, name, desc, module in STANDARD_PERMISSIONS:
        p, _ = AppPermission.objects.get_or_create(
            code=code,
            defaults={"name": name, "description": desc, "module": module, "is_active": True}
        )
        perm_objs[code] = p

    # 1. SUPER_ADMIN Role
    super_role, _ = ApplicationRole.objects.get_or_create(
        code="SUPER_ADMIN",
        defaults={
            "name": "Super Administrator",
            "description": "National / System-wide supreme administration authority.",
            "is_active": True,
            "is_system_role": True,
            "created_by": creator
        }
    )
    # Super Admin gets all permissions
    for p in perm_objs.values():
        RolePermission.objects.get_or_create(role=super_role, permission=p)

    # 2. REGIONAL_ADMIN Role
    reg_role, _ = ApplicationRole.objects.get_or_create(
        code="REGIONAL_ADMIN",
        defaults={
            "name": "Regional Administrator",
            "description": "Regional Command administrative authority over regional data and district administrators.",
            "is_active": True,
            "is_system_role": True,
            "created_by": creator
        }
    )
    reg_perms = [
        "personnel.view", "personnel.create", "personnel.edit",
        "district.manage", "station.manage", "post.manage",
        "admin.assign_district", "admin.revoke_district",
        "role.view",
        "leave.view", "leave.process",
        "transfer.view", "transfer.process",
        "functional.assign",
        "report.view", "audit.view",
    ]
    for code in reg_perms:
        if code in perm_objs:
            RolePermission.objects.get_or_create(role=reg_role, permission=perm_objs[code])

    # 3. DISTRICT_ADMIN Role
    dist_role, _ = ApplicationRole.objects.get_or_create(
        code="DISTRICT_ADMIN",
        defaults={
            "name": "District Administrator",
            "description": "District / Municipal / Metropolitan Command administrative authority.",
            "is_active": True,
            "is_system_role": True,
            "created_by": creator
        }
    )
    dist_perms = [
        "personnel.view", "personnel.create", "personnel.edit",
        "station.manage", "post.manage",
        "leave.view", "leave.process",
        "transfer.view", "transfer.process",
        "report.view",
    ]
    for code in dist_perms:
        if code in perm_objs:
            RolePermission.objects.get_or_create(role=dist_role, permission=perm_objs[code])

    # 4. NATIONAL_ADMIN Role
    nat_role, _ = ApplicationRole.objects.get_or_create(
        code="NATIONAL_ADMIN",
        defaults={
            "name": "National Administrator",
            "description": "National Headquarters operations administration.",
            "is_active": True,
            "is_system_role": True,
            "created_by": creator
        }
    )
    for p in perm_objs.values():
        RolePermission.objects.get_or_create(role=nat_role, permission=p)

    return {
        "roles": [super_role, reg_role, dist_role, nat_role],
        "permissions_count": len(perm_objs)
    }


def get_actor_administrative_scope(actor):
    """
    Return dictionary describing actor's highest administrative scope.
    Levels:
    - NATIONAL (Super Admin / National Admin)
    - REGIONAL (Regional Admin with region)
    - DISTRICT (District Admin with district)
    - NONE (Ordinary staff without administrative roles)
    """
    if not actor or not actor.is_authenticated:
        return {"level": "NONE", "region": None, "district": None}

    if actor.is_superuser:
        return {"level": "NATIONAL", "region": None, "district": None}

    assignment = getattr(actor, 'assignment', None)
    if not assignment or not assignment.active:
        if actor.is_staff:
            return {"level": "NATIONAL", "region": None, "district": None}
        return {"level": "NONE", "region": None, "district": None}

    # Check active AdministrativeAssignment first
    if assignment.personnel:
        active_assignments = assignment.personnel.administrative_assignments.filter(
            is_active=True, role__is_active=True
        ).select_related('role', 'region', 'district').order_by('role__code')

        for asgn in active_assignments:
            if asgn.role.code in ("SUPER_ADMIN", "NATIONAL_ADMIN"):
                return {"level": "NATIONAL", "region": None, "district": None}
            elif asgn.role.code == "REGIONAL_ADMIN" and asgn.region:
                return {"level": "REGIONAL", "region": asgn.region, "district": None}
            elif asgn.role.code == "DISTRICT_ADMIN" and asgn.district:
                return {"level": "DISTRICT", "region": asgn.district.region, "district": asgn.district}

    # Fallback to UserAssignment role
    if assignment.role in ("SUPER_ADMIN", "NATIONAL_ADMIN"):
        return {"level": "NATIONAL", "region": None, "district": None}
    elif assignment.role == "REGIONAL_ADMIN" and assignment.region:
        return {"level": "REGIONAL", "region": assignment.region, "district": None}
    elif assignment.role in ("DFO", "DISTRICT_ADMIN") and assignment.district:
        return {"level": "DISTRICT", "region": assignment.district.region, "district": assignment.district}

    return {"level": "NONE", "region": None, "district": None}


def can_assign_role(actor, target_personnel, role, region=None, district=None, station=None, level=None):
    """
    Validate whether actor is authorized to assign 'role' with given scope to 'target_personnel'.
    Enforces Invariants:
    - Super Admin can assign any role nationally, regionally, or at district level.
    - Regional Admin can assign DISTRICT_ADMIN and functional responsibilities within their assigned Region.
    - District Admin can assign functional responsibilities within their assigned District.
    - NO SELF-PROMOTION: Actor cannot assign themselves a role or expand their scope.
    """
    if not actor or not actor.is_authenticated or not target_personnel or not role:
        return False

    actor_scope = get_actor_administrative_scope(actor)
    actor_level = actor_scope["level"]

    if actor_level == "NONE":
        return False

    # Check target personnel identity
    actor_personnel = getattr(actor, 'assignment', None)
    actor_p_id = actor_personnel.personnel_id if actor_personnel else None

    # NO SELF-PROMOTION: Actor cannot assign themselves
    if actor_p_id and actor_p_id == target_personnel.id:
        return False

    # Station scope validation if provided
    if station:
        if district and station.district_id != district.id:
            return False
        if region and station.district.region_id != region.id:
            return False

    role_code = role.code if isinstance(role, ApplicationRole) else str(role)

    # Super Admin can assign any role anywhere
    if actor_level == "NATIONAL":
        return True

    # Regional Admin rules:
    if actor_level == "REGIONAL":
        actor_region = actor_scope["region"]
        if not actor_region:
            return False
        # Cannot assign National level or to another Region
        if level == "NATIONAL":
            return False
        if region and region.id != actor_region.id:
            return False
        if district and district.region_id != actor_region.id:
            return False
        if station and station.district.region_id != actor_region.id:
            return False

        # Cannot assign National or Regional Admin application roles
        if role_code in ("SUPER_ADMIN", "NATIONAL_ADMIN", "REGIONAL_ADMIN"):
            return False

        # Can assign DISTRICT_ADMIN within their Region
        if role_code == "DISTRICT_ADMIN":
            if district and district.region_id == actor_region.id:
                return True
            return False

        # Can assign functional responsibilities within their Region
        return True

    # District Admin rules:
    if actor_level == "DISTRICT":
        actor_district = actor_scope["district"]
        if not actor_district:
            return False

        # Cannot assign application administration roles
        if role_code in ("SUPER_ADMIN", "NATIONAL_ADMIN", "REGIONAL_ADMIN", "DISTRICT_ADMIN"):
            return False

        # Cannot assign outside their District or at National/Regional level
        if level in ("NATIONAL", "REGIONAL"):
            return False
        if district and district.id != actor_district.id:
            return False
        if station and station.district_id != actor_district.id:
            return False
        if region and actor_district.region_id != region.id:
            return False

        # Can assign functional responsibilities within their District
        return True

    return False


def can_revoke_role(actor, assignment):
    """
    Validate whether actor is authorized to revoke an AdministrativeAssignment.
    Enforces Invariants:
    - Super Admin can revoke any role nationwide (except itself).
    - Regional Admin can revoke assignments within their assigned Region.
    - District Admin can revoke functional assignments within their assigned District.
    - NO SELF-DEMOTION: Administrator cannot revoke their own active administrative assignment.
    - Super Admin account cannot be revoked through ordinary UI.
    """
    if not actor or not actor.is_authenticated or not assignment or not assignment.is_active:
        return False

    actor_scope = get_actor_administrative_scope(actor)
    actor_level = actor_scope["level"]

    if actor_level == "NONE":
        return False

    # Check target personnel identity
    actor_personnel = getattr(actor, 'assignment', None)
    actor_p_id = actor_personnel.personnel_id if actor_personnel else None

    # NO SELF-DEMOTION
    if actor_p_id and actor_p_id == assignment.personnel_id:
        return False

    # Protect Super Admin role from revocation
    if assignment.role and assignment.role.code in ("SUPER_ADMIN", "NATIONAL_ADMIN") and not actor.is_superuser:
        return False

    # Super Admin can revoke any assignment (except self)
    if actor_level == "NATIONAL":
        return True

    # Regional Admin rules:
    if actor_level == "REGIONAL":
        actor_region = actor_scope["region"]
        if not actor_region:
            return False
        # Must be in their own region
        if assignment.region and assignment.region.id != actor_region.id:
            return False
        if assignment.district and assignment.district.region_id != actor_region.id:
            return False
        if assignment.level == "NATIONAL":
            return False
        if assignment.role and assignment.role.code in ("SUPER_ADMIN", "NATIONAL_ADMIN", "REGIONAL_ADMIN"):
            return False
        return True

    # District Admin rules:
    if actor_level == "DISTRICT":
        actor_district = actor_scope["district"]
        if not actor_district:
            return False
        if assignment.role and assignment.role.code in ("SUPER_ADMIN", "NATIONAL_ADMIN", "REGIONAL_ADMIN", "DISTRICT_ADMIN"):
            return False
        if assignment.district and assignment.district.id == actor_district.id:
            return True
        return False

    return False


def assign_role(
    actor,
    personnel,
    role=None,
    level="DISTRICT",
    region=None,
    district=None,
    station=None,
    directorate=None,
    department=None,
    unit=None,
    functional_office=None,
    responsibility_title="",
    effective_from=None,
    remarks="",
    assignment_type=None
):
    """
    Execute assignment of an administrative role or functional responsibility to a personnel member.
    Wraps in a transaction, creates historical AdministrativeAssignment,
    synchronizes FunctionalRoleAssignment or UserAssignment, and logs an audited event.
    """
    level = (level or "DISTRICT").upper()
    role_obj = None

    if assignment_type:
        assignment_type = assignment_type
        if isinstance(role, ApplicationRole):
            role_obj = role
            responsibility_title = responsibility_title or role_obj.name
        elif isinstance(role, str):
            responsibility_title = responsibility_title or role
    elif isinstance(role, ApplicationRole):
        role_obj = role
        assignment_type = AdministrativeAssignment.AssignmentType.ADMINISTRATIVE
        responsibility_title = responsibility_title or role_obj.name
    elif isinstance(role, str) and role in ("SUPER_ADMIN", "NATIONAL_ADMIN", "REGIONAL_ADMIN", "DISTRICT_ADMIN"):
        role_obj = ApplicationRole.objects.filter(code=role).first()
        if role_obj:
            assignment_type = AdministrativeAssignment.AssignmentType.ADMINISTRATIVE
            responsibility_title = responsibility_title or role_obj.name
        else:
            responsibility_title = responsibility_title or role
    else:
        assignment_type = AdministrativeAssignment.AssignmentType.FUNCTIONAL
        responsibility_title = responsibility_title or (str(role) if role else "")

    role_check_val = role_obj or responsibility_title

    if not can_assign_role(actor, personnel, role_check_val, region=region, district=district, station=station, level=level):
        raise PermissionDenied("You are not authorized to assign this administrative role or responsibility.")

    if not personnel.is_active:
        raise ValidationError(f"Cannot assign role to inactive personnel {personnel.fullname}.")

    # Validate scope integrity
    if level == "NATIONAL":
        region = None
        district = None
        station = None
    elif level == "REGIONAL":
        district = None
        station = None
        if not region:
            raise ValidationError("A specific Region must be assigned for Regional scope.")
    elif level == "DISTRICT":
        if station and not district:
            district = station.district
        if not district:
            raise ValidationError("A specific District must be assigned for District scope.")
        if not region:
            region = district.region
        elif district.region_id != region.id:
            raise ValidationError(f"District '{district.name}' does not belong to '{region.name}'.")
        if station and station.district_id != district.id:
            raise ValidationError(f"Station '{station.name}' does not belong to District '{district.name}'.")

    # Validate personnel eligibility according to authoritative current posting
    if level == "DISTRICT":
        if station:
            in_station = (
                (personnel.station_id == station.id) or
                PersonnelPosting.objects.filter(personnel=personnel, is_current=True, station=station).exists()
            )
            if not in_station:
                raise ValidationError(f"Officer {personnel.fullname} ({personnel.staffID}) is not currently posted to {station.name}.")
        elif district:
            in_district = (
                (personnel.station and personnel.station.district_id == district.id) or
                PersonnelPosting.objects.filter(personnel=personnel, is_current=True, district=district).exists()
            )
            if not in_district:
                raise ValidationError(f"Officer {personnel.fullname} ({personnel.staffID}) is not currently posted to {district.name}.")
    elif level == "REGIONAL" and region:
        in_region = (
            (personnel.station and personnel.station.district and personnel.station.district.region_id == region.id) or
            PersonnelPosting.objects.filter(personnel=personnel, is_current=True, region=region).exists()
        )
        if not in_region:
            raise ValidationError(f"Officer {personnel.fullname} ({personnel.staffID}) is not currently posted to {region.name}.")

    effective_from = effective_from or timezone.localdate()

    with transaction.atomic():
        # Duplicate active assignment prevention
        if assignment_type == AdministrativeAssignment.AssignmentType.ADMINISTRATIVE and role_obj:
            existing = AdministrativeAssignment.objects.filter(
                personnel=personnel,
                role=role_obj,
                region=region,
                district=district,
                station=station,
                is_active=True
            ).first()
        else:
            existing = AdministrativeAssignment.objects.filter(
                personnel=personnel,
                assignment_type=AdministrativeAssignment.AssignmentType.FUNCTIONAL,
                responsibility_title=responsibility_title,
                level=level,
                region=region,
                district=district,
                station=station,
                is_active=True
            ).first()

        if existing:
            raise ValidationError(f"Officer {personnel.fullname} already holds an active assignment for '{responsibility_title or (role_obj.name if role_obj else '')}' in this scope.")

        # Resolve department / functional office if needed
        dept_obj = None
        dept_str = ""
        if isinstance(department, FunctionalUnit):
            dept_obj = department
            dept_str = dept_obj.name
        elif isinstance(department, str) and department:
            dept_str = department
            if department.isdigit():
                dept_obj = FunctionalUnit.objects.filter(id=int(department)).first()
                if dept_obj:
                    dept_str = dept_obj.name

        unit_obj = None
        if isinstance(unit, FunctionalUnit):
            unit_obj = unit
        elif isinstance(unit, str) and unit and unit.isdigit():
            unit_obj = FunctionalUnit.objects.filter(id=int(unit)).first()

        # Resolve FunctionalOffice for functional role assignments
        if not functional_office and assignment_type == AdministrativeAssignment.AssignmentType.FUNCTIONAL:
            # Map from dept_str
            dept_upper = dept_str.upper()
            fn_type = None
            if "ACCOUNT" in dept_upper:
                fn_type = FunctionalOffice.FunctionType.ACCOUNTS
            elif "OPERATION" in dept_upper or "INVESTIGATION" in dept_upper:
                fn_type = FunctionalOffice.FunctionType.OPERATIONS
            elif "WATCH" in dept_upper:
                fn_type = FunctionalOffice.FunctionType.WATCH_ROOM
            elif "HR" in dept_upper or "HUMAN RESOURCE" in dept_upper:
                fn_type = FunctionalOffice.FunctionType.HR
            elif "ADMIN" in dept_upper or "SECRETARIAT" in dept_upper:
                fn_type = FunctionalOffice.FunctionType.ADMINISTRATION

            if fn_type:
                fo_qs = FunctionalOffice.objects.filter(function_type=fn_type, active=True)
                if level == "DISTRICT" and district:
                    functional_office = fo_qs.filter(command_level=FunctionalOffice.CommandLevel.DISTRICT, district=district).first()
                elif level == "REGIONAL" and region:
                    functional_office = fo_qs.filter(command_level=FunctionalOffice.CommandLevel.REGIONAL, region=region).first()
                elif level == "NATIONAL":
                    functional_office = fo_qs.filter(command_level=FunctionalOffice.CommandLevel.NATIONAL).first()

        # Create new historical administrative assignment
        asgn = AdministrativeAssignment.objects.create(
            personnel=personnel,
            assignment_type=assignment_type,
            level=level,
            role=role_obj,
            region=region,
            district=district,
            station=station,
            directorate=directorate,
            department=dept_obj,
            unit=unit_obj,
            functional_office=functional_office,
            responsibility_title=responsibility_title,
            remarks=remarks,
            effective_from=effective_from,
            is_active=True,
            assigned_by=actor
        )

        # Synchronize FunctionalRoleAssignment if functional
        if assignment_type == AdministrativeAssignment.AssignmentType.FUNCTIONAL:
            # Determine functional role type
            title_upper = (responsibility_title or "").upper()
            if "HEAD" in title_upper or "DIRECTOR" in title_upper or "OFFICER" in title_upper:
                if level == "NATIONAL":
                    fra_role = FunctionalRoleType.DIRECTORATE_HEAD
                elif level == "REGIONAL":
                    fra_role = FunctionalRoleType.REGIONAL_FUNCTION_HEAD
                else:
                    fra_role = FunctionalRoleType.DISTRICT_FUNCTION_HEAD
            else:
                fra_role = FunctionalRoleType.FUNCTION_MEMBER

            # Find matching user account
            recipient_user = getattr(personnel, 'user_account', None)
            if not recipient_user:
                from django.contrib.auth import get_user_model
                User = get_user_model()
                recipient_user = User.objects.filter(username=personnel.staffID).first()

            fra, created = FunctionalRoleAssignment.objects.get_or_create(
                personnel=personnel,
                functional_office=functional_office,
                defaults={
                    "user": recipient_user,
                    "scope_level": level,
                    "region": region,
                    "district": district,
                    "organisational_unit": dept_obj or directorate or unit_obj,
                    "role": fra_role,
                    "effective_from": effective_from,
                    "assigned_by": actor,
                    "active": True
                }
            )
            if not created and not fra.active:
                fra.active = True
                fra.effective_from = effective_from
                fra.assigned_by = actor
                fra.scope_level = level
                fra.region = region
                fra.district = district
                fra.organisational_unit = dept_obj or directorate or unit_obj
                fra.role = fra_role
                fra.save(update_fields=['active', 'effective_from', 'assigned_by', 'scope_level', 'region', 'district', 'organisational_unit', 'role'])

        # Synchronize UserAssignment if administrative role
        elif assignment_type == AdministrativeAssignment.AssignmentType.ADMINISTRATIVE and role_obj:
            user_asgn = getattr(personnel, 'user_assignment', None)
            if not user_asgn:
                from staff.models import UserAssignment
                user_asgn = UserAssignment.objects.filter(personnel=personnel).first()

            if user_asgn:
                if role_obj.code == "REGIONAL_ADMIN":
                    user_asgn.role = GNFSUserRole.REGIONAL_ADMIN
                    user_asgn.region = region
                elif role_obj.code == "DISTRICT_ADMIN":
                    user_asgn.role = GNFSUserRole.DISTRICT_ADMIN
                    user_asgn.region = region
                    user_asgn.district = district
                elif role_obj.code == "NATIONAL_ADMIN":
                    user_asgn.role = GNFSUserRole.NATIONAL_ADMIN
                user_asgn.save()

        # Audit Log (Section 33)
        scope_str = "National"
        if district:
            scope_str = f"{district.name} District"
        elif region:
            scope_str = f"{region.name} Region"

        action_name = "ROLE_ASSIGNED"
        if assignment_type == AdministrativeAssignment.AssignmentType.ADMINISTRATIVE:
            if role_obj and role_obj.code == "REGIONAL_ADMIN":
                action_name = "REGIONAL_ADMIN_ASSIGNED"
            elif role_obj and role_obj.code == "DISTRICT_ADMIN":
                action_name = "DISTRICT_ADMIN_ASSIGNED"
        else:
            action_name = "FUNCTIONAL_ROLE_ASSIGNED"

        AuditLog.objects.create(
            user=actor,
            action=action_name,
            target_type="Personnel",
            target_id=str(personnel.id),
            description=f"Assigned {responsibility_title or (role_obj.name if role_obj else 'Role')} [{scope_str}] to {personnel.fullname} ({personnel.staffID}). Remarks: {remarks}",
            region=region,
            district=district,
            reason=remarks,
            new_values={
                "actor": actor.username,
                "target_personnel": personnel.fullname,
                "target_staffID": personnel.staffID,
                "assignment_type": assignment_type,
                "level": level,
                "region": region.name if region else None,
                "district": district.name if district else None,
                "directorate": directorate.name if directorate else None,
                "department": dept_str or (dept_obj.name if dept_obj else None),
                "unit": unit_obj.name if unit_obj else (str(unit) if unit else None),
                "role": responsibility_title or (role_obj.name if role_obj else ''),
                "effective_date": str(effective_from),
                "remarks": remarks,
                "timestamp": str(timezone.now())
            }
        )

        # In-App Notification to recipient if linked to user account
        recipient_user = getattr(personnel, 'user_account', None)
        if not recipient_user:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            recipient_user = User.objects.filter(username=personnel.staffID).first()

        if recipient_user:
            Notification.objects.create(
                recipient=recipient_user,
                title="Role & Responsibility Assigned",
                message=f"You have been assigned as '{responsibility_title or (role_obj.name if role_obj else '')}' for {scope_str}.",
                notification_type="SUCCESS",
                link_url="/my/dashboard/"
            )

    return asgn


def revoke_role(actor, assignment, reason=""):
    """
    Revoke an active AdministrativeAssignment.
    Marks is_active=False, sets effective_to, revoked_by, revoked_at, revocation_reason.
    Deactivates corresponding FunctionalRoleAssignment if functional.
    Does NOT delete assignment history, personnel record, or user account.
    """
    if not can_revoke_role(actor, assignment):
        raise PermissionDenied("You are not authorized to revoke this role assignment.")

    if not reason or not reason.strip():
        raise ValidationError("A specific justification reason is mandatory for role revocation.")

    with transaction.atomic():
        assignment.is_active = False
        assignment.effective_to = timezone.localdate()
        assignment.revoked_by = actor
        assignment.revoked_at = timezone.now()
        assignment.revocation_reason = reason.strip()
        assignment.save(update_fields=[
            'is_active', 'effective_to', 'revoked_by', 'revoked_at', 'revocation_reason', 'updated_at'
        ])

        # Deactivate associated FunctionalRoleAssignment if functional
        if assignment.assignment_type == AdministrativeAssignment.AssignmentType.FUNCTIONAL and assignment.functional_office:
            FunctionalRoleAssignment.objects.filter(
                personnel=assignment.personnel,
                functional_office=assignment.functional_office,
                active=True
            ).update(active=False, effective_to=timezone.localdate())

        # Synchronize UserAssignment if administrative role
        personnel = assignment.personnel
        user_asgn = getattr(personnel, 'user_assignment', None)
        if not user_asgn:
            from staff.models import UserAssignment
            user_asgn = UserAssignment.objects.filter(personnel=personnel).first()

        if user_asgn and assignment.assignment_type == AdministrativeAssignment.AssignmentType.ADMINISTRATIVE:
            remaining_admin = personnel.administrative_assignments.filter(
                is_active=True,
                assignment_type=AdministrativeAssignment.AssignmentType.ADMINISTRATIVE
            ).select_related('role', 'region', 'district').first()

            if remaining_admin and remaining_admin.role:
                if remaining_admin.role.code == "REGIONAL_ADMIN":
                    user_asgn.role = GNFSUserRole.REGIONAL_ADMIN
                    user_asgn.region = remaining_admin.region
                    user_asgn.district = None
                elif remaining_admin.role.code == "DISTRICT_ADMIN":
                    user_asgn.role = GNFSUserRole.DISTRICT_ADMIN
                    user_asgn.region = remaining_admin.region
                    user_asgn.district = remaining_admin.district
            else:
                user_asgn.role = GNFSUserRole.STAFF
                if personnel.current_posting:
                    user_asgn.region = personnel.current_posting.region
                    user_asgn.district = personnel.current_posting.district
                    user_asgn.station = personnel.current_posting.station
            user_asgn.save()

        # Audit Log (Section 33)
        scope_str = "National"
        if assignment.district:
            scope_str = f"{assignment.district.name} District"
        elif assignment.region:
            scope_str = f"{assignment.region.name} Region"

        action_name = "ROLE_REVOKED"
        if assignment.role and assignment.role.code == "REGIONAL_ADMIN":
            action_name = "REGIONAL_ADMIN_REVOKED"
        elif assignment.role and assignment.role.code == "DISTRICT_ADMIN":
            action_name = "DISTRICT_ADMIN_REVOKED"
        elif assignment.assignment_type == AdministrativeAssignment.AssignmentType.FUNCTIONAL:
            action_name = "FUNCTIONAL_ROLE_REVOKED"

        AuditLog.objects.create(
            user=actor,
            action=action_name,
            target_type="Personnel",
            target_id=str(personnel.id),
            description=f"Revoked {assignment.display_role_name} [{scope_str}] from {personnel.fullname} ({personnel.staffID}). Reason: {reason}",
            region=assignment.region,
            district=assignment.district,
            reason=reason,
            old_values={"is_active": True},
            new_values={
                "is_active": False,
                "revoked_by": actor.username,
                "revoked_at": str(timezone.now()),
                "reason": reason
            }
        )

        # Notify recipient
        recipient_user = getattr(personnel, 'user_account', None)
        if not recipient_user:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            recipient_user = User.objects.filter(username=personnel.staffID).first()

        if recipient_user:
            Notification.objects.create(
                recipient=recipient_user,
                title="Role & Responsibility Revoked",
                message=f"Your assignment as '{assignment.display_role_name}' for {scope_str} has been revoked. Reason: {reason}",
                notification_type="WARNING",
                link_url="/my/dashboard/"
            )

    return assignment
