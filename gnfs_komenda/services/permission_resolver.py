"""
Centralised Scope & Permission Resolver for GNFS.
Resolves both Command (Geographical) Scope and Functional Scope.
Supports multiple concurrent roles and dynamic workspace switching.
"""

from django.db.models import Q
from gnfs_komenda.models import (
    NorminalRoll, Region, District, Station, Post,
    FunctionalUnit, FunctionalRoleAssignment, FunctionalRoleType
)
from gnfs_komenda.security import get_user_role, get_user_assignment


def get_command_scope(user):
    """
    Return dictionary describing the user's primary command jurisdiction.
    """
    if not user or not user.is_authenticated:
        return {"level": "NONE"}

    if user.is_superuser:
        return {"level": "NATIONAL"}

    # Check if user is linked to an active CommandAppointment (RFO, DFO, MFO, 2IC)
    from gnfs_komenda.security import get_personnel_for_user
    user_personnel = get_personnel_for_user(user)
    if user_personnel:
        cmd_apt = user_personnel.current_command_appointment
        if cmd_apt and getattr(cmd_apt, 'is_active', False):
            if cmd_apt.appointment_type in ('RFO', 'ACTING_RFO'):
                return {"level": "REGIONAL", "region": cmd_apt.region, "appointment": cmd_apt}
            elif cmd_apt.appointment_type in ('REGIONAL_2IC', 'ACTING_REGIONAL_2IC'):
                return {"level": "REGIONAL_2IC", "region": cmd_apt.region, "appointment": cmd_apt}
            elif cmd_apt.appointment_type in ('DFO', 'MFO', 'METRO_FO', 'ACTING_DFO', 'ACTING_MFO', 'ACTING_METRO_FO'):
                return {"level": "DISTRICT", "district": cmd_apt.district, "region": cmd_apt.region, "appointment": cmd_apt}
            elif cmd_apt.appointment_type in ('DISTRICT_2IC', 'ACTING_DISTRICT_2IC'):
                return {"level": "DISTRICT_2IC", "district": cmd_apt.district, "region": cmd_apt.region, "appointment": cmd_apt}

    assignment = get_user_assignment(user)
    if not assignment or not assignment.active:
        if user.is_staff:
            return {"level": "NATIONAL"}
        return {"level": "NONE"}

    role = assignment.role
    if role == "NATIONAL_ADMIN":
        return {"level": "NATIONAL"}
    elif role == "REGIONAL_ADMIN" and assignment.region:
        return {"level": "REGIONAL", "region": assignment.region}
    elif role in ("DFO", "DISTRICT_ADMIN") and assignment.district:
        return {"level": "DISTRICT", "district": assignment.district, "region": assignment.region}
    elif role == "STATION_ADMIN" and assignment.station:
        return {"level": "STATION", "station": assignment.station, "district": assignment.district, "region": assignment.region}
    elif role == "POST_ADMIN" and assignment.post:
        return {"level": "POST", "post": assignment.post, "station": assignment.station, "district": assignment.district, "region": assignment.region}
    elif role == "STAFF":
        return {
            "level": "STAFF",
            "region": assignment.region,
            "district": assignment.district,
            "station": assignment.station,
            "post": assignment.post,
        }

    return {"level": "NONE"}


def can_manage_regional_leadership(user, region):
    """
    Check if user is authorized to manage regional command appointments (RFO & 2IC).
    Scoped: National Admin or Regional Admin/RFO of this specific region.
    District admins cannot manage regional appointments.
    """
    if not user or not user.is_authenticated or not region:
        return False
    if user.is_superuser:
        return True
    role = get_user_role(user)
    if role == "NATIONAL_ADMIN":
        return True
    if role == "REGIONAL_ADMIN":
        assignment = get_user_assignment(user)
        return bool(assignment and assignment.active and assignment.region_id == region.id)
    user_personnel = NorminalRoll.objects.filter(
        staffID__iexact=user.username
    ).first()
    if user_personnel and region.current_rfo and region.current_rfo.personnel_id == user_personnel.id:
        return True
    return False


def can_manage_district_leadership(user, district):
    """
    Check if user is authorized to manage district command appointments (DFO/MFO & 2IC).
    Scoped: National Admin, Regional Admin of this district's region, or District Admin.
    Other districts' administrators cannot manage this district.
    """
    if not user or not user.is_authenticated or not district:
        return False
    if user.is_superuser:
        return True
    role = get_user_role(user)
    if role == "NATIONAL_ADMIN":
        return True
    if role == "REGIONAL_ADMIN":
        assignment = get_user_assignment(user)
        return bool(assignment and assignment.active and district.region and assignment.region_id == district.region.id)
    if role in ("DFO", "DISTRICT_ADMIN"):
        assignment = get_user_assignment(user)
        return bool(assignment and assignment.active and assignment.district_id == district.id)
    user_personnel = NorminalRoll.objects.filter(
        staffID__iexact=user.username
    ).first()
    if user_personnel:
        if district.current_commanding_officer and district.current_commanding_officer.personnel_id == user_personnel.id:
            return True
        if district.region and district.region.current_rfo and district.region.current_rfo.personnel_id == user_personnel.id:
            return True
    return False


def can_manage_functional_structure(user):
    """
    Check if user can create, update, or deactivate Directorates, Departments, Units.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    role = get_user_role(user)
    return role == "NATIONAL_ADMIN"



def get_functional_scopes(user):
    """
    Return active FunctionalRoleAssignment records for the user.
    """
    if not user or not user.is_authenticated:
        return FunctionalRoleAssignment.objects.none()

    return FunctionalRoleAssignment.objects.filter(
        user=user,
        active=True
    ).select_related('organisational_unit', 'organisational_unit__parent')


def get_managed_functional_unit_ids(user):
    """
    Return set of all FunctionalUnit IDs that user has management authority over,
    including recursive children/descendants.
    """
    if not user or not user.is_authenticated:
        return set()

    if user.is_superuser or get_user_role(user) == "NATIONAL_ADMIN":
        return set(FunctionalUnit.objects.filter(active=True).values_list('id', flat=True))

    managed_ids = set()
    active_roles = get_functional_scopes(user)

    for assignment in active_roles:
        unit = assignment.organisational_unit
        if assignment.role in (
            FunctionalRoleType.DIRECTORATE_HEAD,
            FunctionalRoleType.DEPARTMENT_HEAD,
            FunctionalRoleType.UNIT_HEAD,
            FunctionalRoleType.SECTION_HEAD,
            FunctionalRoleType.UNIT_ADMIN,
        ):
            descendant_ids = unit.get_descendant_ids(include_self=True)
            managed_ids.update(descendant_ids)

    return managed_ids


class WorkspaceCode:
    MY_PORTAL = "MY_PORTAL"
    DISTRICT_ADMINISTRATION = "DISTRICT_ADMINISTRATION"
    DISTRICT_OPERATIONS = "DISTRICT_OPERATIONS"
    DISTRICT_ACCOUNTS = "DISTRICT_ACCOUNTS"
    DISTRICT_WATCH_ROOM = "DISTRICT_WATCH_ROOM"
    DISTRICT_COMMAND_DFO_MFO = "DISTRICT_COMMAND_DFO_MFO"
    DISTRICT_COMMAND_2IC = "DISTRICT_COMMAND_2IC"
    REGIONAL_ADMINISTRATION = "REGIONAL_ADMINISTRATION"
    REGIONAL_OPERATIONS = "REGIONAL_OPERATIONS"
    REGIONAL_ACCOUNTS = "REGIONAL_ACCOUNTS"
    REGIONAL_COMMAND_RFO = "REGIONAL_COMMAND_RFO"
    REGIONAL_COMMAND_2IC = "REGIONAL_COMMAND_2IC"
    NATIONAL_ADMINISTRATION = "NATIONAL_ADMINISTRATION"
    NATIONAL_OPERATIONS = "NATIONAL_OPERATIONS"
    NATIONAL_ACCOUNTS = "NATIONAL_ACCOUNTS"
    NATIONAL_COMMAND = "NATIONAL_COMMAND"


def get_available_workspaces(user):
    """
    Return list of authorized workspaces for the user:
    0. My Portal (Personal self-service for every personnel-linked or authenticated user)
    1. Operational Command Workspaces (RFO, Regional 2IC, DFO/MFO, District 2IC)
    2. Administrative Workspaces (National Admin, Regional Admin, District Admin)
    3. Functional Workspaces (Directorate, Department, Unit)
    4. Functional Offices Workspaces (Accounts, Operations, Administration, Watch Room)
    """
    if not user or not user.is_authenticated:
        return []

    workspaces = []
    ws_ids = set()

    def add_ws(ws_dict):
        ws_dict.setdefault("region_id", None)
        ws_dict.setdefault("district_id", None)
        ws_dict.setdefault("name", ws_dict.get("label", ""))
        ws_dict.setdefault("label", ws_dict.get("name", ""))
        ws_id = ws_dict.get("id")
        if ws_id and ws_id not in ws_ids:
            workspaces.append(ws_dict)
            ws_ids.add(ws_id)

    # 0. Personal Self-Service Workspace: MY PORTAL (Every Personnel & authenticated user)
    from gnfs_komenda.security import get_personnel_for_user
    user_personnel = get_personnel_for_user(user)
    if user_personnel:
        rank_str = user_personnel.rank.abbreviation if user_personnel.rank else "Personnel"
        add_ws({
            "id": "my_dashboard",
            "code": WorkspaceCode.MY_PORTAL,
            "type": "SELF_SERVICE",
            "label": "My Portal",
            "name": "My Portal",
            "sub_label": f"{rank_str} • {user_personnel.staffID}",
            "url": "/my/dashboard/",
        })
    else:
        add_ws({
            "id": "my_dashboard",
            "code": WorkspaceCode.MY_PORTAL,
            "type": "SELF_SERVICE",
            "label": "My Portal",
            "name": "My Portal",
            "sub_label": user.username,
            "url": "/my/dashboard/",
        })

    # 1. Operational Command Workspaces (from CommandAppointment)
    if user_personnel:
        for cmd_apt in user_personnel.command_appointments.filter(is_active=True).select_related('region', 'district'):
            apt_type = cmd_apt.appointment_type
            if apt_type in ('RFO', 'ACTING_RFO') and cmd_apt.region:
                r = cmd_apt.region
                add_ws({
                    "id": f"cmd_reg_{r.id}",
                    "code": WorkspaceCode.REGIONAL_COMMAND_RFO,
                    "type": "COMMAND",
                    "label": f"{r.name} Command",
                    "name": f"{r.name} Command",
                    "sub_label": "Regional Command (RFO)",
                    "region_id": r.id,
                    "url": f"/dashboard/region/{r.id}/",
                })
            elif apt_type in ('REGIONAL_2IC', 'ACTING_REGIONAL_2IC') and cmd_apt.region:
                r = cmd_apt.region
                add_ws({
                    "id": f"cmd_reg_2ic_{r.id}",
                    "code": WorkspaceCode.REGIONAL_COMMAND_2IC,
                    "type": "COMMAND",
                    "label": f"{r.name} 2IC Command",
                    "name": f"{r.name} 2IC Command",
                    "sub_label": "Regional 2IC",
                    "region_id": r.id,
                    "url": f"/dashboard/region/{r.id}/",
                })
            elif apt_type in ('DFO', 'MFO', 'METRO_FO', 'ACTING_DFO', 'ACTING_MFO', 'ACTING_METRO_FO') and cmd_apt.district:
                d = cmd_apt.district
                co_label = "MFO" if getattr(d, 'co_designation', 'DFO') == 'MFO' else "DFO"
                add_ws({
                    "id": f"cmd_dist_{d.id}",
                    "code": WorkspaceCode.DISTRICT_COMMAND_DFO_MFO,
                    "type": "COMMAND",
                    "label": f"{d.name} Command",
                    "name": f"{d.name} Command",
                    "sub_label": f"District Command ({co_label})",
                    "district_id": d.id,
                    "region_id": d.region_id,
                    "url": f"/dashboard/district/{d.id}/command/",
                })
            elif apt_type in ('DISTRICT_2IC', 'ACTING_DISTRICT_2IC') and cmd_apt.district:
                d = cmd_apt.district
                add_ws({
                    "id": f"cmd_dist_2ic_{d.id}",
                    "code": WorkspaceCode.DISTRICT_COMMAND_2IC,
                    "type": "COMMAND",
                    "label": f"{d.name} 2IC Command",
                    "name": f"{d.name} 2IC Command",
                    "sub_label": "District 2IC",
                    "district_id": d.id,
                    "region_id": d.region_id,
                    "url": f"/dashboard/district/{d.id}/command/",
                })

    # 2. Administrative Workspaces (from UserAssignment and AdministrativeAssignment)
    if user.is_superuser:
        add_ws({
            "id": "cmd_national",
            "code": WorkspaceCode.NATIONAL_ADMINISTRATION,
            "type": "ADMINISTRATION",
            "label": "National Administration",
            "name": "National Administration",
            "sub_label": "Nationwide Command & Admin",
            "url": "/dashboard/national/",
        })
    else:
        assignment = get_user_assignment(user)
        role = get_user_role(user)

        # Check AdministrativeAssignment
        if user_personnel:
            active_admin_asgns = user_personnel.administrative_assignments.filter(
                is_active=True, role__is_active=True
            ).select_related('role', 'region', 'district')
            for a_asgn in active_admin_asgns:
                a_code = a_asgn.role.code
                if a_code in ("SUPER_ADMIN", "NATIONAL_ADMIN"):
                    add_ws({
                        "id": "cmd_national",
                        "code": WorkspaceCode.NATIONAL_ADMINISTRATION,
                        "type": "ADMINISTRATION",
                        "label": "National Administration",
                        "name": "National Administration",
                        "sub_label": "National Headquarters",
                        "url": "/dashboard/national/",
                    })
                elif a_code == "REGIONAL_ADMIN" and a_asgn.region:
                    r = a_asgn.region
                    ws_id = f"admin_reg_{r.id}" if f"cmd_reg_{r.id}" in ws_ids else f"cmd_reg_{r.id}"
                    add_ws({
                        "id": ws_id,
                        "code": WorkspaceCode.REGIONAL_ADMINISTRATION,
                        "type": "ADMINISTRATION",
                        "label": f"{r.name} Administration",
                        "name": f"{r.name} Administration",
                        "sub_label": "Regional Administrator",
                        "region_id": r.id,
                        "url": f"/dashboard/region/{r.id}/",
                    })
                elif a_code in ("DFO", "MFO", "COMMANDING_OFFICER") and a_asgn.district:
                    d = a_asgn.district
                    co_label = "MFO" if getattr(d, 'co_designation', 'DFO') == 'MFO' else "DFO"
                    add_ws({
                        "id": f"cmd_dist_{d.id}",
                        "code": WorkspaceCode.DISTRICT_COMMAND_DFO_MFO,
                        "type": "COMMAND",
                        "label": f"{d.name} Command",
                        "name": f"{d.name} Command",
                        "sub_label": f"District Command ({co_label})",
                        "district_id": d.id,
                        "region_id": d.region_id,
                        "url": f"/dashboard/district/{d.id}/command/",
                    })
                elif a_code in ("DISTRICT_2IC", "2IC") and a_asgn.district:
                    d = a_asgn.district
                    add_ws({
                        "id": f"cmd_dist_2ic_{d.id}",
                        "code": WorkspaceCode.DISTRICT_COMMAND_2IC,
                        "type": "COMMAND",
                        "label": f"{d.name} 2IC Command",
                        "name": f"{d.name} 2IC Command",
                        "sub_label": "District 2IC",
                        "district_id": d.id,
                        "region_id": d.region_id,
                        "url": f"/dashboard/district/{d.id}/command/",
                    })
                elif a_code == "DISTRICT_ADMIN" and a_asgn.district:
                    d = a_asgn.district
                    ws_id = f"cmd_dist_{d.id}" if f"cmd_dist_{d.id}" not in ws_ids else f"admin_dist_{d.id}"
                    add_ws({
                        "id": ws_id,
                        "code": WorkspaceCode.DISTRICT_ADMINISTRATION,
                        "type": "ADMINISTRATION",
                        "label": f"{d.name} Administration",
                        "name": f"{d.name} Administration",
                        "sub_label": "District Administrator",
                        "district_id": d.id,
                        "region_id": d.region_id,
                        "url": f"/workspace/admin/district/{d.id}/",
                    })

        # Fallback to UserAssignment role if not already added
        if role == "NATIONAL_ADMIN":
            add_ws({
                "id": "cmd_national",
                "code": WorkspaceCode.NATIONAL_ADMINISTRATION,
                "type": "ADMINISTRATION",
                "label": "National Administration",
                "name": "National Administration",
                "sub_label": "National Headquarters",
                "url": "/dashboard/national/",
            })
        elif role == "REGIONAL_ADMIN" and assignment and assignment.region:
            r = assignment.region
            ws_id = f"admin_reg_{r.id}" if f"cmd_reg_{r.id}" in ws_ids else f"cmd_reg_{r.id}"
            add_ws({
                "id": ws_id,
                "code": WorkspaceCode.REGIONAL_ADMINISTRATION,
                "type": "ADMINISTRATION",
                "label": f"{r.name} Administration",
                "name": f"{r.name} Administration",
                "sub_label": "Regional Administrator",
                "region_id": r.id,
                "url": f"/dashboard/region/{r.id}/",
            })
        elif role in ("DFO", "DISTRICT_ADMIN") and assignment and assignment.district:
            d = assignment.district
            ws_id = f"cmd_dist_{d.id}" if f"cmd_dist_{d.id}" not in ws_ids else f"admin_dist_{d.id}"
            add_ws({
                "id": ws_id,
                "code": WorkspaceCode.DISTRICT_ADMINISTRATION,
                "type": "ADMINISTRATION",
                "label": f"{d.name} Administration",
                "name": f"{d.name} Administration",
                "sub_label": "District Administrator",
                "district_id": d.id,
                "region_id": d.region_id,
                "url": f"/workspace/admin/district/{d.id}/",
            })
        elif role == "STATION_ADMIN" and assignment and assignment.station:
            s = assignment.station
            add_ws({
                "id": f"cmd_stn_{s.id}",
                "type": "ADMINISTRATION",
                "label": s.name,
                "name": s.name,
                "sub_label": "Fire Station",
                "url": f"/dashboard/station/{s.id}/",
            })
        elif role == "POST_ADMIN" and assignment and assignment.post:
            p = assignment.post
            add_ws({
                "id": f"cmd_post_{p.id}",
                "type": "ADMINISTRATION",
                "label": p.name,
                "name": p.name,
                "sub_label": "Fire Post",
                "url": f"/dashboard/post/{p.id}/",
            })

    # 3. Functional Workspaces
    func_roles = get_functional_scopes(user)
    for fa in func_roles:
        unit = fa.organisational_unit
        if unit:
            add_ws({
                "id": f"func_{unit.id}",
                "type": "FUNCTIONAL",
                "label": unit.name,
                "name": unit.name,
                "sub_label": f"{unit.get_unit_type_display()} • {fa.get_role_display()}",
                "unit_id": unit.id,
                "unit_type": unit.unit_type,
                "role": fa.role,
                "url": f"/dashboard/functional/{unit.id}/",
            })

    # 4. Functional Offices Workspaces (Accounts Nominal Roll, Administration, Operations, HR, Watch Room)
    try:
        from gnfs_komenda.services.functional_scope_service import get_user_authorized_workspaces
        func_office_ws = get_user_authorized_workspaces(user)
        for f_ws in func_office_ws:
            if f_ws.get("id") != "my_dashboard":
                add_ws(f_ws)
    except Exception:
        pass

    return workspaces


def get_active_workspace(request):
    """
    Get the currently active workspace dictionary from session.
    Defaults to the first available workspace.
    """
    workspaces = get_available_workspaces(request.user)
    if not workspaces:
        return None

    active_id = request.session.get("gnfs_active_workspace_id")
    if active_id:
        for ws in workspaces:
            if ws["id"] == active_id:
                return ws

    # Default to first workspace
    first_ws = workspaces[0]
    request.session["gnfs_active_workspace_id"] = first_ws["id"]
    return first_ws


def can_view_personnel(user, personnel):
    """
    Check if user is authorized to view this personnel record.
    Allowed if officer is in user's command scope, administrative scope, or is own record.
    """
    if not user or not user.is_authenticated or not personnel:
        return False
    if user.is_superuser or get_user_role(user) == "NATIONAL_ADMIN":
        return True

    from gnfs_komenda.security import get_personnel_for_user
    user_personnel = get_personnel_for_user(user)
    if user_personnel and user_personnel.id == personnel.id:
        return True
    if user.username.upper() == personnel.staffID.upper():
        return True

    # Check Administrative Scope
    from gnfs_komenda.services.role_service import get_actor_administrative_scope
    admin_scope = get_actor_administrative_scope(user)
    if admin_scope["level"] == "NATIONAL":
        return True
    elif admin_scope["level"] == "REGIONAL" and admin_scope["region"]:
        if personnel.current_region_id == admin_scope["region"].id:
            return True
    elif admin_scope["level"] == "DISTRICT" and admin_scope["district"]:
        if personnel.current_district_id == admin_scope["district"].id:
            return True

    # Check Command Scope
    cmd_scope = get_command_scope(user)
    level = cmd_scope.get("level")

    if level == "REGIONAL" and personnel.current_region == cmd_scope.get("region"):
        return True
    elif level == "DISTRICT" and personnel.current_district == cmd_scope.get("district"):
        return True
    elif level == "STATION" and personnel.station == cmd_scope.get("station"):
        return True
    elif level == "POST" and personnel.post == cmd_scope.get("post"):
        return True

    # Check Functional Scope
    if personnel.functional_unit_id:
        managed_unit_ids = get_managed_functional_unit_ids(user)
        if personnel.functional_unit_id in managed_unit_ids:
            return True

    return False


def can_manage_personnel(user, personnel):
    """
    Check if user has edit/management authority over this personnel record.
    Ordinary staff cannot manage personnel records.
    """
    if not user or not user.is_authenticated or not personnel:
        return False
    if user.is_superuser or get_user_role(user) == "NATIONAL_ADMIN":
        return True

    from gnfs_komenda.services.role_service import get_actor_administrative_scope
    admin_scope = get_actor_administrative_scope(user)
    if admin_scope["level"] == "NATIONAL":
        return True
    elif admin_scope["level"] == "REGIONAL" and admin_scope["region"]:
        return personnel.current_region_id == admin_scope["region"].id
    elif admin_scope["level"] == "DISTRICT" and admin_scope["district"]:
        return personnel.current_district_id == admin_scope["district"].id

    # Check Functional Scope
    managed_unit_ids = get_managed_functional_unit_ids(user)
    if managed_unit_ids:
        if personnel.functional_unit_id and personnel.functional_unit_id in managed_unit_ids:
            return True
        if hasattr(personnel, 'functional_assignments') and personnel.functional_assignments.filter(functional_unit_id__in=managed_unit_ids, is_active=True).exists():
            return True

    return False


def can_manage_region(user, region):
    """Check if user can manage regional administration or leadership."""
    if not user or not user.is_authenticated or not region:
        return False
    if user.is_superuser or get_user_role(user) == "NATIONAL_ADMIN":
        return True
    from gnfs_komenda.services.role_service import get_actor_administrative_scope
    admin_scope = get_actor_administrative_scope(user)
    if admin_scope["level"] == "REGIONAL" and admin_scope["region"] and admin_scope["region"].id == region.id:
        return True
    cmd_scope = get_command_scope(user)
    if cmd_scope.get("level") == "REGIONAL" and cmd_scope.get("region") and cmd_scope["region"].id == region.id:
        return True
    return False


def can_manage_district(user, district):
    """Check if user can manage district administration or leadership."""
    if not user or not user.is_authenticated or not district:
        return False
    if user.is_superuser or get_user_role(user) == "NATIONAL_ADMIN":
        return True
    from gnfs_komenda.services.role_service import get_actor_administrative_scope
    admin_scope = get_actor_administrative_scope(user)
    if admin_scope["level"] == "REGIONAL" and admin_scope["region"] and district.region_id == admin_scope["region"].id:
        return True
    if admin_scope["level"] == "DISTRICT" and admin_scope["district"] and admin_scope["district"].id == district.id:
        return True
    cmd_scope = get_command_scope(user)
    if cmd_scope.get("level") == "REGIONAL" and cmd_scope.get("region") and district.region_id == cmd_scope["region"].id:
        return True
    if cmd_scope.get("level") == "DISTRICT" and cmd_scope.get("district") and cmd_scope["district"].id == district.id:
        return True
    return False


def can_assign_district_admin(user, district):
    """Only Super Admin or Regional Admin of the district's region can assign District Admin."""
    if not user or not user.is_authenticated or not district:
        return False
    if user.is_superuser:
        return True
    from gnfs_komenda.services.role_service import get_actor_administrative_scope
    admin_scope = get_actor_administrative_scope(user)
    if admin_scope["level"] == "NATIONAL":
        return True
    if admin_scope["level"] == "REGIONAL" and admin_scope["region"] and district.region_id == admin_scope["region"].id:
        return True
    return False


def can_process_leave(user, leave_request):
    """Central check for administrative leave review & verification."""
    from gnfs_komenda.services.leave_routing_service import can_user_process_leave as _proc
    return _proc(user, leave_request)


def can_approve_leave(user, leave_request):
    """Central check for executive command leave approval (DFO / RFO)."""
    from gnfs_komenda.services.leave_routing_service import can_user_final_approve_leave as _appr
    return _appr(user, leave_request)


def can_view_workspace(user, workspace_id):
    """Check if workspace_id is among the user's authorized workspaces."""
    workspaces = get_available_workspaces(user)
    return any(ws["id"] == workspace_id for ws in workspaces)


def can_manage_functional_unit(user, unit):
    """
    Check if user has administrative management authority over a FunctionalUnit.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or get_user_role(user) == "NATIONAL_ADMIN":
        return True

    managed_ids = get_managed_functional_unit_ids(user)
    return unit.id in managed_ids


def get_scoped_personnel_queryset(user, workspace=None, include_archived=False):
    """
    Return NorminalRoll queryset filtered to the specified workspace or overall user authority.
    Ordinary staff without administrative/command authority only see their own record.
    """
    if not user or not user.is_authenticated:
        return NorminalRoll.objects.none()

    base_qs = NorminalRoll.objects.all() if include_archived else NorminalRoll.active_objects.all()
    base_qs = base_qs.select_related(
        'station', 'station__district', 'station__district__region',
        'post', 'rank', 'functional_unit'
    )

    if user.is_superuser or get_user_role(user) == "NATIONAL_ADMIN":
        if workspace and workspace.get("type") == "FUNCTIONAL":
            unit_id = workspace.get("unit_id")
            unit = FunctionalUnit.objects.filter(id=unit_id).first()
            if unit:
                descendants = unit.get_descendant_ids(include_self=True)
                return base_qs.filter(functional_unit_id__in=descendants)
        return base_qs

    # If specific functional workspace is active
    if workspace and workspace.get("type") == "FUNCTIONAL":
        unit_id = workspace.get("unit_id")
        unit = FunctionalUnit.objects.filter(id=unit_id).first()
        if unit and can_manage_functional_unit(user, unit):
            descendants = unit.get_descendant_ids(include_self=True)
            return base_qs.filter(functional_unit_id__in=descendants)
        return NorminalRoll.objects.none()

    from gnfs_komenda.services.role_service import get_actor_administrative_scope
    admin_scope = get_actor_administrative_scope(user)
    cmd_scope = get_command_scope(user)

    # Regional Authority
    if admin_scope["level"] == "REGIONAL" and admin_scope["region"]:
        return base_qs.filter(station__district__region=admin_scope["region"])
    if cmd_scope.get("level") == "REGIONAL" and cmd_scope.get("region"):
        return base_qs.filter(station__district__region=cmd_scope["region"])

    # District Authority
    if admin_scope["level"] == "DISTRICT" and admin_scope["district"]:
        return base_qs.filter(station__district=admin_scope["district"])
    if cmd_scope.get("level") == "DISTRICT" and cmd_scope.get("district"):
        return base_qs.filter(station__district=cmd_scope["district"])

    # Station Authority
    if cmd_scope.get("level") == "STATION" and cmd_scope.get("station"):
        return base_qs.filter(station=cmd_scope["station"])

    # Post Authority
    if cmd_scope.get("level") == "POST" and cmd_scope.get("post"):
        return base_qs.filter(post=cmd_scope["post"])

    # Ordinary Staff: strictly see only their own record
    return base_qs.filter(staffID__iexact=user.username)

