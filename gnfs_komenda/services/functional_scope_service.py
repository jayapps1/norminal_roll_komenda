"""
Centralized Functional Scope & Multi-Dimensional Authorization Resolver for GNFS.
Implements:
- Section 1-5: Command Structure vs Functional Structure
- Section 11-18: Accounts / Nominal Roll scoped functional views
- Section 19-35: Administration & Operations functional workspaces
- Section 36-39: HR & Fallback Responsibility
- Section 40-43: Watch Room operational recording
- Section 74-78: Functional Scope Resolver
- Section 79: Workspace Switcher Resolver
"""

import logging
from django.db.models import Q
from django.urls import reverse
from django.core.exceptions import PermissionDenied

from gnfs_komenda.models import (
    NorminalRoll, Region, District, FunctionalOffice,
    FunctionalRoleAssignment, FunctionalRoleType,
    PersonnelFunctionalAssignment, AdministrativeAssignment
)
from staff.models import GNFSUserRole

logger = logging.getLogger(__name__)


def get_functional_scope(user, function_type):
    """
    Determine the actor's authorized operational command level and geographic scope
    for a given functional area ('ADMINISTRATION', 'ACCOUNTS', 'OPERATIONS', 'HR', 'WATCH_ROOM').
    Evaluates:
    1. National Super Administrator override
    2. Explicit FunctionalRoleAssignment
    3. Configured Fallback Responsibility (e.g. District Admin acting for unestablished HR)
    4. AdministrativeAssignment (Regional Admin / District Admin)
    """
    if not user or not user.is_authenticated:
        return {
            "authorized": False,
            "level": "NONE",
            "region": None,
            "district": None,
            "office": None,
            "effective_office": None,
            "role": None,
            "is_head": False,
            "fallback_applied": False,
        }

    # 1. Superuser / National Admin Override (Section 95)
    if user.is_superuser or user.groups.filter(name="NATIONAL_ADMIN").exists():
        national_office = FunctionalOffice.objects.filter(
            function_type=function_type,
            command_level=FunctionalOffice.CommandLevel.NATIONAL
        ).first()
        return {
            "authorized": True,
            "level": "NATIONAL",
            "region": None,
            "district": None,
            "office": national_office,
            "effective_office": national_office,
            "role": FunctionalRoleType.DIRECTORATE_HEAD,
            "is_head": True,
            "fallback_applied": False,
        }

    # Match personnel record for user if available
    user_personnel = NorminalRoll.objects.filter(staffID__iexact=user.username).first()

    # 2. Check explicit active FunctionalRoleAssignment
    q_assignment = Q(active=True) & (Q(user=user) | (Q(personnel=user_personnel) if user_personnel else Q(pk__in=[])))
    direct_assignment = FunctionalRoleAssignment.objects.filter(
        q_assignment,
        functional_office__function_type=function_type
    ).select_related('functional_office', 'region', 'district').first()

    if direct_assignment and direct_assignment.functional_office:
        office = direct_assignment.functional_office
        effective = office.get_effective_office()
        is_head = direct_assignment.role in (
            FunctionalRoleType.DIRECTORATE_HEAD,
            FunctionalRoleType.REGIONAL_FUNCTION_HEAD,
            FunctionalRoleType.DISTRICT_FUNCTION_HEAD
        )
        return {
            "authorized": True,
            "level": direct_assignment.scope_level or office.command_level,
            "region": direct_assignment.region or office.region,
            "district": direct_assignment.district or office.district,
            "office": office,
            "effective_office": effective,
            "role": direct_assignment.role,
            "is_head": is_head,
            "fallback_applied": False,
        }

    # 3. Check Fallback Responsibility for HR (Section 2, 3, 37, 78, 86)
    # If function is HR and no direct HR assignment, check if user holds ADMINISTRATION assignment
    # in an office where HR is not established and delegates to Administration!
    if function_type == FunctionalOffice.FunctionType.HR:
        admin_assignment = FunctionalRoleAssignment.objects.filter(
            q_assignment,
            functional_office__function_type=FunctionalOffice.FunctionType.ADMINISTRATION
        ).select_related('functional_office', 'region', 'district').first()

        if admin_assignment and admin_assignment.functional_office:
            adm_office = admin_assignment.functional_office
            # Check if there is an unestablished HR office falling back to this Administration office
            unestablished_hr = FunctionalOffice.objects.filter(
                function_type=FunctionalOffice.FunctionType.HR,
                is_established=False,
                fallback_office=adm_office,
                active=True
            ).first()

            if unestablished_hr:
                is_head = admin_assignment.role in (
                    FunctionalRoleType.DIRECTORATE_HEAD,
                    FunctionalRoleType.REGIONAL_FUNCTION_HEAD,
                    FunctionalRoleType.DISTRICT_FUNCTION_HEAD
                )
                return {
                    "authorized": True,
                    "level": adm_office.command_level,
                    "region": adm_office.region,
                    "district": adm_office.district,
                    "office": unestablished_hr,
                    "effective_office": adm_office,
                    "role": admin_assignment.role,
                    "is_head": is_head,
                    "fallback_applied": True,
                }

    # 4. Check AdministrativeAssignment (Regional Admin / District Admin / Functional)
    if user_personnel:
        admin_assign_qs = AdministrativeAssignment.objects.filter(
            personnel=user_personnel,
            is_active=True
        ).select_related('role', 'region', 'district', 'functional_office', 'unit')
    else:
        admin_assign_qs = AdministrativeAssignment.objects.none()

    # 4a. Check direct functional office assignment in AdministrativeAssignment
    direct_admin_func = admin_assign_qs.filter(functional_office__function_type=function_type).first()
    if direct_admin_func:
        off = direct_admin_func.functional_office
        return {
            "authorized": True,
            "level": direct_admin_func.level or (off.command_level if off else "DISTRICT"),
            "region": direct_admin_func.region or (off.region if off else None),
            "district": direct_admin_func.district or (off.district if off else None),
            "office": off,
            "effective_office": off,
            "role": direct_admin_func.display_role_name,
            "is_head": True,
            "fallback_applied": False,
        }

    # 4b. Check by Role code or title
    for admin_assign in admin_assign_qs:
        role_code = admin_assign.role.code if admin_assign.role else ""
        title = admin_assign.responsibility_title or ""

        # Regional Admin
        if role_code == "REGIONAL_ADMIN":
            reg_office = FunctionalOffice.objects.filter(
                function_type=function_type,
                command_level=FunctionalOffice.CommandLevel.REGIONAL,
                region=admin_assign.region
            ).first()

            if function_type in (FunctionalOffice.FunctionType.ADMINISTRATION, FunctionalOffice.FunctionType.HR):
                return {
                    "authorized": True,
                    "level": "REGIONAL",
                    "region": admin_assign.region,
                    "district": None,
                    "office": reg_office,
                    "effective_office": reg_office,
                    "role": FunctionalRoleType.REGIONAL_FUNCTION_HEAD,
                    "is_head": True,
                    "fallback_applied": False,
                }

        # District Admin
        if role_code in ("DISTRICT_ADMIN", "DFO"):
            dist_office = FunctionalOffice.objects.filter(
                function_type=function_type,
                command_level=FunctionalOffice.CommandLevel.DISTRICT,
                district=admin_assign.district
            ).first()

            if function_type in (FunctionalOffice.FunctionType.ADMINISTRATION, FunctionalOffice.FunctionType.HR):
                fallback = (function_type == FunctionalOffice.FunctionType.HR)
                return {
                    "authorized": True,
                    "level": "DISTRICT",
                    "region": admin_assign.district.region if admin_assign.district else None,
                    "district": admin_assign.district,
                    "office": dist_office,
                    "effective_office": dist_office,
                    "role": FunctionalRoleType.DISTRICT_FUNCTION_HEAD,
                    "is_head": True,
                    "fallback_applied": fallback,
                }

        # Functional roles within AdministrativeAssignment
        if function_type in ("OPERATIONS", "ACCOUNTS", "WATCH_ROOM"):
            matches_fn = (
                function_type.lower() in title.lower() or
                (admin_assign.unit and function_type.lower() in admin_assign.unit.name.lower())
            )
            if matches_fn:
                dist_office = FunctionalOffice.objects.filter(
                    function_type=function_type,
                    district=admin_assign.district
                ).first()
                return {
                    "authorized": True,
                    "level": admin_assign.level or "DISTRICT",
                    "region": admin_assign.region or (admin_assign.district.region if admin_assign.district else None),
                    "district": admin_assign.district,
                    "office": dist_office,
                    "effective_office": dist_office,
                    "role": FunctionalRoleType.DISTRICT_FUNCTION_HEAD,
                    "is_head": True,
                    "fallback_applied": False,
                }

    # 5. Check staff UserAssignment fallback
    from staff.models import UserAssignment
    u_asgn = getattr(user, 'assignment', None) or UserAssignment.objects.filter(user=user, active=True).first()
    if u_asgn and u_asgn.active:
        if u_asgn.role == "REGIONAL_ADMIN" and u_asgn.region and function_type in ("ADMINISTRATION", "HR"):
            return {
                "authorized": True,
                "level": "REGIONAL",
                "region": u_asgn.region,
                "district": None,
                "office": None,
                "effective_office": None,
                "role": FunctionalRoleType.REGIONAL_FUNCTION_HEAD,
                "is_head": True,
                "fallback_applied": False,
            }
        elif u_asgn.role in ("DISTRICT_ADMIN", "DFO") and u_asgn.district and function_type in ("ADMINISTRATION", "HR"):
            return {
                "authorized": True,
                "level": "DISTRICT",
                "region": u_asgn.district.region if u_asgn.district else None,
                "district": u_asgn.district,
                "office": None,
                "effective_office": None,
                "role": FunctionalRoleType.DISTRICT_FUNCTION_HEAD,
                "is_head": True,
                "fallback_applied": (function_type == "HR"),
            }

    # 6. Check PersonnelFunctionalAssignment
    if user_personnel:
        pfa = PersonnelFunctionalAssignment.objects.filter(
            personnel=user_personnel,
            is_active=True
        ).filter(
            Q(functional_unit__name__icontains=function_type) |
            Q(assignment_role__icontains=function_type)
        ).select_related('functional_unit').first()
        if pfa:
            p_dist = user_personnel.current_district or (user_personnel.station.district if user_personnel.station else None)
            p_reg = user_personnel.current_region or (p_dist.region if p_dist else None)
            return {
                "authorized": True,
                "level": "DISTRICT" if p_dist else ("REGIONAL" if p_reg else "NATIONAL"),
                "region": p_reg,
                "district": p_dist,
                "office": None,
                "effective_office": None,
                "role": pfa.assignment_role or "MEMBER",
                "is_head": False,
                "fallback_applied": False,
            }

    return {
        "authorized": False,
        "level": "NONE",
        "region": None,
        "district": None,
        "office": None,
        "effective_office": None,
        "role": None,
        "is_head": False,
        "fallback_applied": False,
    }


def get_scoped_nominal_roll(user, district_id=None, region_id=None, search_query=None):
    """
    Accounts Workspace: Authoritative scoped functional view of the ONE Personnel database (Section 11-18).
    Strict backend invariant: Scope is applied FIRST before any search query.
    - District Accounts: ONLY sees personnel under its District.
    - Regional Accounts: ONLY sees personnel under districts in its Region.
    - National Accounts: Sees all regions/districts nationally.
    """
    scope = get_functional_scope(user, FunctionalOffice.FunctionType.ACCOUNTS)
    is_national = user.is_superuser or scope["level"] == "NATIONAL"

    if not (is_national or scope["authorized"]):
        raise PermissionDenied("You are not authorized to view the Accounts Nominal Roll.")

    base_qs = NorminalRoll.active_objects.select_related('rank', 'station', 'station__district', 'station__district__region')

    # Apply Scope Filter FIRST (Section 18)
    if is_national:
        if district_id:
            scoped_qs = base_qs.filter(station__district_id=district_id)
        elif region_id:
            scoped_qs = base_qs.filter(station__district__region_id=region_id)
        else:
            scoped_qs = base_qs
    elif scope["level"] == "REGIONAL":
        reg = scope["region"]
        if district_id:
            scoped_qs = base_qs.filter(station__district_id=district_id, station__district__region=reg)
        else:
            scoped_qs = base_qs.filter(station__district__region=reg)
    elif scope["level"] == "DISTRICT":
        dist = scope["district"]
        scoped_qs = base_qs.filter(station__district=dist)
    else:
        raise PermissionDenied("Unauthorized nominal roll scope.")

    # Search Query applied within authorized scope ONLY
    if search_query:
        q = search_query.strip()
        scoped_qs = scoped_qs.filter(
            Q(fullname__icontains=q) |
            Q(staffID__icontains=q) |
            Q(rank__name__icontains=q) |
            Q(station__name__icontains=q)
        )

    return scoped_qs.order_by('rank__hierarchy_order', 'fullname')


def get_scoped_operational_personnel(user, district_id=None, region_id=None, search_query=None):
    """
    Operations Workspace: Authoritative operational availability view (Section 29-35).
    Scopes operational personnel visibility by command level:
    - District Operations: Operational personnel in that district.
    - Regional Operations: Operational personnel across all districts in region.
    - National Operations: Nationwide operations visibility.
    Operations sees ONLY Personnel within its authorised Operations functional scope.
    """
    scope = get_functional_scope(user, FunctionalOffice.FunctionType.OPERATIONS)
    is_national = user.is_superuser or scope["level"] == "NATIONAL"

    if not (is_national or scope["authorized"]):
        raise PermissionDenied("You are not authorized to view the Operations Workspace.")

    base_qs = NorminalRoll.active_objects.select_related('rank', 'station', 'station__district', 'station__district__region')

    # Apply Scope Filter
    if is_national:
        if district_id:
            scoped_qs = base_qs.filter(station__district_id=district_id)
        elif region_id:
            scoped_qs = base_qs.filter(station__district__region_id=region_id)
        else:
            scoped_qs = base_qs
    elif scope["level"] == "REGIONAL":
        reg = scope["region"]
        if district_id:
            scoped_qs = base_qs.filter(station__district_id=district_id, station__district__region=reg)
        else:
            scoped_qs = base_qs.filter(station__district__region=reg)
    elif scope["level"] == "DISTRICT":
        dist = scope["district"]
        scoped_qs = base_qs.filter(station__district=dist)
    else:
        raise PermissionDenied("Unauthorized operations scope.")

    # Strictly filter to Personnel within Operations functional scope (Prompt 2 Section 8 & 9)
    ops_filter = Q(
        functional_assignments__functional_unit__name__icontains="Operations",
        functional_assignments__is_active=True
    ) | Q(
        functional_assignments__functional_office__function_type="OPERATIONS",
        functional_assignments__is_active=True
    )
    non_ops_filter = Q(
        functional_assignments__functional_unit__name__iregex=r'(Accounts|Finance|Audit|Human Resource|HR|Legal|Welfare)',
        functional_assignments__is_active=True
    ) | Q(
        functional_assignments__functional_office__function_type__in=["ACCOUNTS", "HR"],
        functional_assignments__is_active=True
    )

    if scoped_qs.filter(ops_filter).exists():
        scoped_qs = scoped_qs.filter(ops_filter).distinct()
    else:
        # Exclude purely administrative / accounts / non-operational staff
        scoped_qs = scoped_qs.exclude(non_ops_filter).distinct()

    if search_query:
        q = search_query.strip()
        scoped_qs = scoped_qs.filter(
            Q(fullname__icontains=q) |
            Q(staffID__icontains=q) |
            Q(rank__name__icontains=q)
        )

    return scoped_qs.order_by('rank__hierarchy_order', 'fullname')


def get_user_authorized_workspaces(user):
    """
    Resolves all functional and command workspaces the user is authorized to enter (Section 79).
    Powers the Workspace Switcher in the navigation bar.
    Provides complete ID, Code, Label, Name, Type, and URL metadata.
    """
    if not user or not user.is_authenticated:
        return []

    workspaces = []

    # 1. My Portal (Always available for every personnel user)
    workspaces.append({
        "id": "my_dashboard",
        "code": "MY_PORTAL",
        "name": "My Portal",
        "label": "My Portal",
        "sub_label": "Personal Portal",
        "type": "SELF_SERVICE",
        "url": reverse("my-dashboard"),
        "category": "Self-Service",
        "badge": "Personal",
        "icon": "fa-user-circle"
    })

    # 2. Superuser / National Dashboards
    if user.is_superuser or user.groups.filter(name="NATIONAL_ADMIN").exists():
        workspaces.append({
            "id": "cmd_national",
            "code": "NATIONAL_COMMAND",
            "name": "National Command",
            "label": "National Command",
            "sub_label": "National Headquarters",
            "type": "COMMAND",
            "url": reverse("dashboard-national"),
            "category": "National",
            "badge": "Command",
            "icon": "fa-shield-alt"
        })
        workspaces.append({
            "id": "accounts_national",
            "code": "NATIONAL_ACCOUNTS",
            "name": "National Accounts / Nominal Roll",
            "label": "National Accounts / Nominal Roll",
            "sub_label": "National Accounts Oversight",
            "type": "ACCOUNTS",
            "url": reverse("workspace-accounts-national"),
            "category": "National",
            "badge": "Accounts",
            "icon": "fa-file-invoice-dollar"
        })
        workspaces.append({
            "id": "admin_national",
            "code": "NATIONAL_ADMINISTRATION",
            "name": "National Administration",
            "label": "National Administration",
            "sub_label": "National Headquarters Admin",
            "type": "ADMINISTRATION",
            "url": reverse("workspace-admin-national"),
            "category": "National",
            "badge": "Admin",
            "icon": "fa-building"
        })
        workspaces.append({
            "id": "ops_national",
            "code": "NATIONAL_OPERATIONS",
            "name": "National Operations",
            "label": "National Operations",
            "sub_label": "Nationwide Operational Readiness",
            "type": "OPERATIONS",
            "url": reverse("workspace-ops-national"),
            "category": "National",
            "badge": "Operations",
            "icon": "fa-fire-extinguisher"
        })
        return workspaces

    # 3. Check Regional Functional Scopes
    for fn in ("ADMINISTRATION", "ACCOUNTS", "OPERATIONS", "HR"):
        sc = get_functional_scope(user, fn)
        if sc["authorized"] and sc["level"] == "REGIONAL" and sc["region"]:
            r_name = sc["region"].name
            r_id = sc["region"].id
            if fn == "ADMINISTRATION":
                workspaces.append({
                    "id": f"admin_reg_{r_id}",
                    "code": "REGIONAL_ADMINISTRATION",
                    "name": f"{r_name} Administration",
                    "label": f"{r_name} Administration",
                    "sub_label": "Regional Administrator",
                    "type": "ADMINISTRATION",
                    "url": reverse("workspace-admin-regional", kwargs={"region_id": r_id}),
                    "category": "Regional",
                    "badge": "Admin",
                    "icon": "fa-building",
                    "region_id": r_id,
                })
            elif fn == "ACCOUNTS":
                workspaces.append({
                    "id": f"accounts_reg_{r_id}",
                    "code": "REGIONAL_ACCOUNTS",
                    "name": f"{r_name} Accounts / Nominal Roll",
                    "label": f"{r_name} Accounts / Nominal Roll",
                    "sub_label": "Regional Accounts & Nominal Roll",
                    "type": "ACCOUNTS",
                    "url": reverse("workspace-accounts-regional", kwargs={"region_id": r_id}),
                    "category": "Regional",
                    "badge": "Accounts",
                    "icon": "fa-file-invoice-dollar",
                    "region_id": r_id,
                })
            elif fn == "OPERATIONS":
                workspaces.append({
                    "id": f"ops_reg_{r_id}",
                    "code": "REGIONAL_OPERATIONS",
                    "name": f"{r_name} Operations",
                    "label": f"{r_name} Operations",
                    "sub_label": "Regional Operations Force",
                    "type": "OPERATIONS",
                    "url": reverse("workspace-ops-regional", kwargs={"region_id": r_id}),
                    "category": "Regional",
                    "badge": "Operations",
                    "icon": "fa-fire-extinguisher",
                    "region_id": r_id,
                })

    # 4. Check District Functional Scopes
    for fn in ("ADMINISTRATION", "ACCOUNTS", "OPERATIONS", "HR", "WATCH_ROOM"):
        sc = get_functional_scope(user, fn)
        if sc["authorized"] and sc["level"] == "DISTRICT" and sc["district"]:
            d_name = sc["district"].name
            d_id = sc["district"].id
            if fn == "ADMINISTRATION":
                workspaces.append({
                    "id": f"cmd_dist_{d_id}",
                    "code": "DISTRICT_ADMINISTRATION",
                    "name": f"{d_name} Administration",
                    "label": f"{d_name} Administration",
                    "sub_label": "District Personnel Administration",
                    "type": "ADMINISTRATION",
                    "url": reverse("workspace-admin-district", kwargs={"district_id": d_id}),
                    "category": "District",
                    "badge": "Admin",
                    "icon": "fa-building",
                    "district_id": d_id,
                    "region_id": sc["district"].region_id,
                })
            elif fn == "ACCOUNTS":
                workspaces.append({
                    "id": f"accounts_dist_{d_id}",
                    "code": "DISTRICT_ACCOUNTS",
                    "name": f"{d_name} Accounts",
                    "label": f"{d_name} Accounts",
                    "sub_label": "District Accounts & Nominal Roll",
                    "type": "ACCOUNTS",
                    "url": reverse("workspace-accounts-district", kwargs={"district_id": d_id}),
                    "category": "District",
                    "badge": "Accounts",
                    "icon": "fa-file-invoice-dollar",
                    "district_id": d_id,
                    "region_id": sc["district"].region_id,
                })
            elif fn == "OPERATIONS":
                workspaces.append({
                    "id": f"ops_dist_{d_id}",
                    "code": "DISTRICT_OPERATIONS",
                    "name": f"{d_name} Operations",
                    "label": f"{d_name} Operations",
                    "sub_label": "District Operations Readiness",
                    "type": "OPERATIONS",
                    "url": reverse("workspace-ops-district", kwargs={"district_id": d_id}),
                    "category": "District",
                    "badge": "Operations",
                    "icon": "fa-fire-extinguisher",
                    "district_id": d_id,
                    "region_id": sc["district"].region_id,
                })
            elif fn == "WATCH_ROOM":
                workspaces.append({
                    "id": f"watch_room_dist_{d_id}",
                    "code": "DISTRICT_WATCH_ROOM",
                    "name": f"{d_name} Watch Room",
                    "label": f"{d_name} Watch Room",
                    "sub_label": "Station Diary & Resumptions",
                    "type": "WATCH_ROOM",
                    "url": reverse("workspace-watch-room-district", kwargs={"district_id": d_id}),
                    "category": "District",
                    "badge": "Watch Room",
                    "icon": "fa-clock",
                    "district_id": d_id,
                    "region_id": sc["district"].region_id,
                })

    return workspaces
