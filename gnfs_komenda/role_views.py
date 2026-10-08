"""
Role & Administrative Access Management Views for GNFS.
Implements Sections 8-18, 39-45, 62-66 of the GNFS National Specification.
Provides:
- Role & Access Overview (/admin/roles/)
- Assign Administrative Role (/admin/roles/assign/)
- Revoke Administrative Role (/admin/roles/revoke/<assignment_id>/)
- Configure Role Permissions (/admin/roles/<role_id>/permissions/)
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from gnfs_komenda.forms import AdministrativeRoleAssignmentForm, AdministrativeRoleRevocationForm
from gnfs_komenda.models import (
    ApplicationRole, AppPermission, RolePermission,
    AdministrativeAssignment, AuditLog, Region, District, Station, NorminalRoll,
    FunctionalUnit, FunctionalOffice, PersonnelPosting
)
from gnfs_komenda.security import get_personnel_queryset_for_user, get_districts_queryset_for_user
from gnfs_komenda.services.role_service import (
    get_actor_administrative_scope, can_assign_role, can_revoke_role,
    assign_role, revoke_role
)


@login_required(login_url='login-staff')
def role_list_view(request):
    """
    Overview of Application Roles, Active Administrative Assignments, and History (Section 8, 62).
    """
    actor_scope = get_actor_administrative_scope(request.user)
    if actor_scope["level"] == "NONE":
        raise PermissionDenied("You are not authorized to view Administrative Role Management.")

    is_national = actor_scope["level"] == "NATIONAL"

    if is_national:
        active_assignments = AdministrativeAssignment.objects.filter(
            is_active=True
        ).select_related('personnel', 'role', 'region', 'district', 'assigned_by').order_by('-assigned_at')
        
        history_assignments = AdministrativeAssignment.objects.filter(
            is_active=False
        ).select_related('personnel', 'role', 'region', 'district', 'assigned_by', 'revoked_by').order_by('-revoked_at')[:20]
        
        roles = ApplicationRole.objects.all().prefetch_related('permissions').order_by('is_system_role', 'name')
    else:
        # Regional Admin scoped view
        reg = actor_scope["region"]
        active_assignments = AdministrativeAssignment.objects.filter(
            is_active=True,
            region=reg
        ).select_related('personnel', 'role', 'region', 'district', 'assigned_by').order_by('-assigned_at')

        history_assignments = AdministrativeAssignment.objects.filter(
            is_active=False,
            region=reg
        ).select_related('personnel', 'role', 'region', 'district', 'assigned_by', 'revoked_by').order_by('-revoked_at')[:20]

        roles = ApplicationRole.objects.filter(code__in=["DISTRICT_ADMIN"])

    # Recent Audit Log Entries for role changes
    audit_logs = AuditLog.objects.filter(
        action__in=[
            "ROLE_ASSIGNED", "ROLE_REVOKED", "ROLE_CREATED", "ROLE_UPDATED",
            "REGIONAL_ADMIN_ASSIGNED", "REGIONAL_ADMIN_REVOKED",
            "DISTRICT_ADMIN_ASSIGNED", "DISTRICT_ADMIN_REVOKED"
        ]
    ).select_related('user').order_by('-timestamp')[:15]

    revocation_form = AdministrativeRoleRevocationForm()

    return render(request, "gnfs_komenda/roles/role_list.html", {
        "is_national": is_national,
        "actor_scope": actor_scope,
        "active_assignments": active_assignments,
        "history_assignments": history_assignments,
        "roles": roles,
        "audit_logs": audit_logs,
        "revocation_form": revocation_form,
    })


@login_required(login_url='login-staff')
def api_organisational_cascade_view(request):
    """
    Unified Cascading AJAX endpoint for Organisational Scope & Functional Responsibilities.
    Actions supported:
    - get_districts: returns districts belonging to region_id within user's authorization.
    - get_directorates: returns National directorates from FunctionalUnit.
    - get_departments: returns departments/functional offices for the selected level/directorate/region/district.
    - get_units: returns configured units under selected department.
    - get_roles: returns valid roles/responsibilities for selected functional area & level.
    """
    action = request.GET.get('action', '').strip()
    actor_scope = get_actor_administrative_scope(request.user)
    if actor_scope["level"] == "NONE":
        return JsonResponse({"error": "Unauthorized"}, status=403)

    if action == "get_districts":
        region_id = request.GET.get('region_id')
        if not region_id:
            return JsonResponse({"districts": []})

        # Security check: if actor is regional admin, verify region_id matches
        if actor_scope["level"] == "REGIONAL" and actor_scope["region"] and str(actor_scope["region"].id) != str(region_id):
            return JsonResponse({"districts": []})

        region = Region.objects.filter(id=region_id, active=True).first()
        if not region:
            return JsonResponse({"districts": []})

        qs = get_districts_queryset_for_user(request.user, region=region)
        data = [
            {
                "id": d.id,
                "name": d.name,
                "code": d.code or "",
                "display": f"{d.name} Fire Command"
            }
            for d in qs
        ]
        return JsonResponse({"districts": data})

    elif action == "get_stations":
        district_id = request.GET.get('district_id')
        if not district_id:
            return JsonResponse({"stations": []})

        # Security check: if actor is district admin, verify district_id matches
        if actor_scope["level"] == "DISTRICT" and actor_scope["district"] and str(actor_scope["district"].id) != str(district_id):
            return JsonResponse({"stations": []})

        district = District.objects.filter(id=district_id, active=True).first()
        if not district:
            return JsonResponse({"stations": []})

        # Security check: if actor is regional admin, verify district belongs to their region
        if actor_scope["level"] == "REGIONAL" and actor_scope["region"] and district.region_id != actor_scope["region"].id:
            return JsonResponse({"stations": []})

        qs = Station.objects.filter(district=district, active=True).order_by('name')
        data = [
            {
                "id": s.id,
                "name": s.name,
                "display": f"{s.name} Fire Station" if "station" not in s.name.lower() else s.name
            }
            for s in qs
        ]
        return JsonResponse({"stations": data})

    elif action == "get_directorates":
        level = (request.GET.get('level') or 'NATIONAL').upper()
        if level != "NATIONAL":
            return JsonResponse({"directorates": []})
        directorates = FunctionalUnit.objects.filter(
            unit_type=FunctionalUnit.FunctionalUnitType.DIRECTORATE,
            active=True
        ).order_by('display_order', 'name')
        data = [{"id": d.id, "name": d.name, "code": d.code or ""} for d in directorates]
        return JsonResponse({"directorates": data})

    elif action == "get_departments":
        level = (request.GET.get('level') or '').upper()

        if level == "NATIONAL":
            directorate_id = request.GET.get('directorate_id')
            if not directorate_id:
                return JsonResponse({"departments": []})
            departments = FunctionalUnit.objects.filter(
                parent_id=directorate_id,
                unit_type__in=[FunctionalUnit.FunctionalUnitType.DEPARTMENT, FunctionalUnit.FunctionalUnitType.DIVISION],
                active=True
            ).order_by('display_order', 'name')
            data = [{"id": str(d.id), "name": d.name, "code": d.code or ""} for d in departments]
            if not data:
                # If no child departments, return the directorate itself as department option
                dir_obj = FunctionalUnit.objects.filter(id=directorate_id, active=True).first()
                if dir_obj:
                    data = [{"id": str(dir_obj.id), "name": f"{dir_obj.name} (General)", "code": dir_obj.code or ""}]
            return JsonResponse({"departments": data})

        elif level == "REGIONAL":
            region_id = request.GET.get('region_id')
            if not region_id:
                return JsonResponse({"departments": []})

            # Check Regional functional offices in database
            offices = FunctionalOffice.objects.filter(
                command_level=FunctionalOffice.CommandLevel.REGIONAL,
                region_id=region_id,
                is_established=True,
                active=True
            ).order_by('name')

            data = []
            seen_types = set()
            for o in offices:
                if o.function_type not in seen_types:
                    seen_types.add(o.function_type)
                    data.append({
                        "id": o.function_type,
                        "name": o.get_function_type_display(),
                        "office_id": o.id
                    })

            if not data:
                for f_code, f_name in [("ADMINISTRATION", "Administration"), ("ACCOUNTS", "Accounts"), ("OPERATIONS", "Operations"), ("HR", "Human Resource")]:
                    data.append({"id": f_code, "name": f_name})

            return JsonResponse({"departments": data})

        elif level == "DISTRICT":
            district_id = request.GET.get('district_id')
            if not district_id:
                return JsonResponse({"departments": []})

            # Check District functional offices in database (exclude unestablished HR, etc.)
            offices = FunctionalOffice.objects.filter(
                command_level=FunctionalOffice.CommandLevel.DISTRICT,
                district_id=district_id,
                is_established=True,
                active=True
            ).order_by('name')

            data = []
            seen_types = set()
            for o in offices:
                if o.function_type not in seen_types:
                    seen_types.add(o.function_type)
                    data.append({
                        "id": o.function_type,
                        "name": o.get_function_type_display(),
                        "office_id": o.id
                    })

            if not data:
                for f_code, f_name in [
                    ("ADMINISTRATION", "Administration"),
                    ("ACCOUNTS", "Accounts"),
                    ("OPERATIONS", "Operations"),
                    ("WATCH_ROOM", "Watch Room")
                ]:
                    data.append({"id": f_code, "name": f_name})

            return JsonResponse({"departments": data})

        return JsonResponse({"departments": []})

    elif action == "get_units":
        department = request.GET.get('department', '').strip()

        units = []
        if department.isdigit():
            sub_units = FunctionalUnit.objects.filter(
                parent_id=int(department),
                unit_type__in=[FunctionalUnit.FunctionalUnitType.UNIT, FunctionalUnit.FunctionalUnitType.SECTION],
                active=True
            ).order_by('display_order', 'name')
            units = [{"id": str(u.id), "name": u.name} for u in sub_units]
        else:
            dept_upper = department.upper()
            if dept_upper in ("ADMINISTRATION", "ADMIN"):
                admin_units = FunctionalUnit.objects.filter(
                    parent__name__icontains="Administration",
                    active=True
                ).order_by('name')
                units = [{"id": str(u.id), "name": u.name} for u in admin_units]
            elif dept_upper in ("OPERATIONS", "OPS"):
                ops_units = FunctionalUnit.objects.filter(
                    parent__name__icontains="Operations",
                    active=True
                ).order_by('name')
                units = [{"id": str(u.id), "name": u.name} for u in ops_units]

        return JsonResponse({"units": units})

    elif action == "get_roles":
        level = (request.GET.get('level') or '').upper()
        department = request.GET.get('department', '').strip()
        dept_upper = department.upper()

        roles = []
        is_national_admin = actor_scope["level"] == "NATIONAL"
        is_regional_admin = actor_scope["level"] in ("NATIONAL", "REGIONAL")

        if level == "DISTRICT":
            if "OPERATION" in dept_upper:
                roles = [
                    {"id": "District Operations Officer", "name": "District Operations Officer"},
                    {"id": "Operations Duty Officer", "name": "Operations Duty Officer"},
                    {"id": "Operations Schedule Officer", "name": "Operations Schedule Officer"}
                ]
            elif "ACCOUNT" in dept_upper or "FINANCE" in dept_upper:
                roles = [
                    {"id": "District Accounts Officer", "name": "District Accounts Officer"},
                    {"id": "Accounts Schedule Officer", "name": "Accounts Schedule Officer"}
                ]
            elif "ADMIN" in dept_upper:
                roles = [
                    {"id": "District Administration Officer", "name": "District Administration Officer"},
                    {"id": "Administration Schedule Officer", "name": "Administration Schedule Officer"}
                ]
                if is_regional_admin:
                    roles.insert(0, {"id": "DISTRICT_ADMIN", "name": "District Administrator (Application Admin)"})
            elif "WATCH" in dept_upper:
                roles = [
                    {"id": "Watch Room Officer", "name": "Watch Room Officer"},
                    {"id": "Watch Room Attendant", "name": "Watch Room Attendant"}
                ]
            elif "HR" in dept_upper or "HUMAN" in dept_upper:
                roles = [
                    {"id": "District Human Resource Officer", "name": "District Human Resource Officer"}
                ]
            else:
                roles = [
                    {"id": f"District {department.title()} Officer", "name": f"District {department.title()} Officer"},
                    {"id": f"District {department.title()} Schedule Officer", "name": f"District {department.title()} Schedule Officer"}
                ]

        elif level == "REGIONAL":
            if "OPERATION" in dept_upper:
                roles = [
                    {"id": "Regional Operations Officer", "name": "Regional Operations Officer"},
                    {"id": "Regional Operations Duty Officer", "name": "Regional Operations Duty Officer"}
                ]
            elif "ACCOUNT" in dept_upper or "FINANCE" in dept_upper:
                roles = [
                    {"id": "Regional Accounts Officer", "name": "Regional Accounts Officer"}
                ]
            elif "ADMIN" in dept_upper:
                roles = [
                    {"id": "Regional Administration Officer", "name": "Regional Administration Officer"}
                ]
                if is_national_admin:
                    roles.insert(0, {"id": "REGIONAL_ADMIN", "name": "Regional Administrator (Application Admin)"})
            elif "HR" in dept_upper or "HUMAN" in dept_upper:
                roles = [
                    {"id": "Regional Human Resource Officer", "name": "Regional Human Resource Officer"}
                ]
            else:
                roles = [
                    {"id": f"Regional {department.title()} Officer", "name": f"Regional {department.title()} Officer"}
                ]

        elif level == "NATIONAL":
            roles = [
                {"id": "Directorate Head", "name": "Directorate Head"},
                {"id": "Department Head", "name": "Department Head"},
                {"id": "Unit Head", "name": "Unit Head"},
                {"id": "National Operations Officer", "name": "National Operations Officer"},
                {"id": "National Accounts Officer", "name": "National Accounts Officer"},
                {"id": "National Administration Officer", "name": "National Administration Officer"},
                {"id": "National Human Resource Officer", "name": "National Human Resource Officer"},
                {"id": "Schedule Officer", "name": "Schedule Officer"},
                {"id": "Officer / Member", "name": "Officer / Member"}
            ]
            if is_national_admin:
                roles.insert(0, {"id": "NATIONAL_ADMIN", "name": "National Administrator (Application Admin)"})

        return JsonResponse({"roles": roles})

    return JsonResponse({"error": "Unknown action"}, status=400)


@login_required(login_url='login-staff')
def api_roles_personnel_search_view(request):
    """
    Select2 Remote AJAX Personnel Search Endpoint.
    Enforces strict actor authorization and selected organizational scope.
    Supports Staff ID, Full Name, Rank, Service Number search.
    Returns 20 items per page with Select2 pagination.
    """
    actor_scope = get_actor_administrative_scope(request.user)
    if actor_scope["level"] == "NONE":
        return HttpResponseForbidden("Forbidden: Administrative role management permission required.")

    q = request.GET.get('q', '').strip()
    level = (request.GET.get('level') or '').upper()
    region_id = request.GET.get('region')
    district_id = request.GET.get('district')
    station_id = request.GET.get('station')
    page_num = request.GET.get('page', 1)

    # 1. Start from authorized personnel queryset for actor
    scoped_qs = get_personnel_queryset_for_user(request.user)

    # 2. Regional Admin boundary lock
    if actor_scope["level"] == "REGIONAL":
        actor_reg = actor_scope["region"]
        scoped_qs = scoped_qs.filter(
            Q(station__district__region=actor_reg) |
            Q(postings__is_current=True, postings__region=actor_reg)
        )
        if region_id and str(region_id) != str(actor_reg.id):
            return JsonResponse({"results": [], "pagination": {"more": False}})
        if district_id:
            d_obj = District.objects.filter(id=district_id, region=actor_reg).first()
            if not d_obj:
                return JsonResponse({"results": [], "pagination": {"more": False}})
        if station_id:
            s_obj = Station.objects.filter(id=station_id, district__region=actor_reg).first()
            if not s_obj:
                return JsonResponse({"results": [], "pagination": {"more": False}})

    # 3. District Admin boundary lock
    elif actor_scope["level"] == "DISTRICT":
        actor_dist = actor_scope["district"]
        scoped_qs = scoped_qs.filter(
            Q(station__district=actor_dist) |
            Q(postings__is_current=True, postings__district=actor_dist)
        )
        if district_id and str(district_id) != str(actor_dist.id):
            return JsonResponse({"results": [], "pagination": {"more": False}})
        if region_id and str(region_id) != str(actor_dist.region_id):
            return JsonResponse({"results": [], "pagination": {"more": False}})
        if station_id:
            s_obj = Station.objects.filter(id=station_id, district=actor_dist).first()
            if not s_obj:
                return JsonResponse({"results": [], "pagination": {"more": False}})

    # 4. Scope First, Search Second: Selected organizational scope filtering:
    if level == "DISTRICT":
        if station_id:
            if district_id:
                stn = Station.objects.filter(id=station_id, district_id=district_id).first()
                if not stn:
                    return JsonResponse({"results": [], "pagination": {"more": False}})
            scoped_qs = scoped_qs.filter(
                Q(station_id=station_id) |
                Q(postings__is_current=True, postings__station_id=station_id)
            )
        elif district_id:
            scoped_qs = scoped_qs.filter(
                Q(station__district_id=district_id) |
                Q(postings__is_current=True, postings__district_id=district_id)
            )
        else:
            return JsonResponse({"results": [], "pagination": {"more": False}})

    elif level == "REGIONAL":
        if region_id:
            scoped_qs = scoped_qs.filter(
                Q(station__district__region_id=region_id) |
                Q(postings__is_current=True, postings__region_id=region_id)
            )
        else:
            return JsonResponse({"results": [], "pagination": {"more": False}})

    # Anti-self-promotion: exclude actor's own personnel record
    actor_personnel = getattr(request.user, 'assignment', None)
    if actor_personnel and actor_personnel.personnel_id:
        scoped_qs = scoped_qs.exclude(id=actor_personnel.personnel_id)

    # 5. Apply search query
    if q:
        scoped_qs = scoped_qs.filter(
            Q(fullname__icontains=q) |
            Q(staffID__icontains=q) |
            Q(rank__name__icontains=q) |
            Q(rank__abbreviation__icontains=q) |
            Q(job_grade__icontains=q)
        )

    scoped_qs = scoped_qs.select_related(
        'rank', 'station', 'station__district', 'station__district__region'
    ).order_by('fullname').distinct()

    # 6. Select2 Pagination (20 per page)
    paginator = Paginator(scoped_qs, 20)
    try:
        page_obj = paginator.page(page_num)
    except Exception:
        page_obj = paginator.page(1)

    results = []
    for p in page_obj:
        stn_name = p.station.name if p.station else ""
        dist_name = p.station.district.name if p.station and p.station.district else ""
        reg_name = p.station.district.region.name if p.station and p.station.district and p.station.district.region else ""

        posting_parts = [part for part in [reg_name, dist_name, stn_name] if part]
        posting_display = " → ".join(posting_parts) if posting_parts else "National Command"

        rank_str = p.rank_display or p.job_grade or "Officer"
        rank_short = p.rank_short or p.job_grade or "Staff"

        results.append({
            "id": p.id,
            "text": f"{p.fullname} — {p.staffID}",
            "name": p.fullname,
            "fullname": p.fullname,
            "staffID": p.staffID,
            "rank": rank_str,
            "rank_short": rank_short,
            "station": stn_name or "N/A",
            "district": dist_name or "N/A",
            "region": reg_name or "N/A",
            "posting_display": posting_display,
            "profile_picture_url": p.profile_picture_url if hasattr(p, 'profile_picture_url') else "",
            "leave_status": p.leave_status
        })

    return JsonResponse({
        "results": results,
        "pagination": {
            "more": page_obj.has_next()
        }
    })


@login_required(login_url='login-staff')
def assign_administrative_role_view(request):
    """
    Assign an administrative role or functional responsibility to a personnel member.
    Enforces hierarchical scope, scope authority, and anti-self-promotion rules.
    """
    actor_scope = get_actor_administrative_scope(request.user)
    if actor_scope["level"] == "NONE":
        raise PermissionDenied("You are not authorized to assign roles or responsibilities.")

    if request.method == 'POST':
        form = AdministrativeRoleAssignmentForm(request.POST, actor=request.user)
        if form.is_valid():
            personnel = form.cleaned_data['personnel']
            level = form.cleaned_data['level']
            region = form.cleaned_data.get('region')
            district = form.cleaned_data.get('district')
            station = form.cleaned_data.get('station')
            directorate = form.cleaned_data.get('directorate')
            department = form.cleaned_data.get('department')
            unit = form.cleaned_data.get('unit')
            role = form.cleaned_data['role']
            effective_from = form.cleaned_data['effective_from']
            remarks = form.cleaned_data.get('remarks', '')

            try:
                assignment = assign_role(
                    actor=request.user,
                    personnel=personnel,
                    role=role,
                    level=level,
                    region=region,
                    district=district,
                    station=station,
                    directorate=directorate,
                    department=department,
                    unit=unit,
                    responsibility_title=role,
                    effective_from=effective_from,
                    remarks=remarks
                )
                messages.success(
                    request,
                    f"Successfully assigned '{assignment.display_role_name}' to {personnel.fullname} ({personnel.staffID})."
                )
                return redirect('role-list')
            except (ValidationError, PermissionDenied) as e:
                messages.error(request, str(e))
    else:
        initial = {'effective_from': timezone.localdate()}
        level = request.GET.get('level')
        if level:
            initial['level'] = level

        personnel_id = request.GET.get('personnel_id')
        if personnel_id:
            p = NorminalRoll.active_objects.filter(id=personnel_id).first()
            if p:
                initial['personnel'] = p

        station_id = request.GET.get('station_id')
        if station_id:
            stn = Station.objects.filter(id=station_id).first()
            if stn:
                initial['station'] = stn
                initial['district'] = stn.district
                initial['region'] = stn.district.region
                initial['level'] = 'DISTRICT'

        district_id = request.GET.get('district_id')
        if district_id and not initial.get('district'):
            d = District.objects.filter(id=district_id).first()
            if d:
                initial['district'] = d
                initial['region'] = d.region
                initial['level'] = 'DISTRICT'

        region_id = request.GET.get('region_id')
        if region_id and not initial.get('region'):
            r = Region.objects.filter(id=region_id).first()
            if r:
                initial['region'] = r

        form = AdministrativeRoleAssignmentForm(initial=initial, actor=request.user)

    return render(request, "gnfs_komenda/roles/assign_role.html", {
        "form": form,
        "actor_scope": actor_scope,
    })


@require_POST
@login_required(login_url='login-staff')
def revoke_administrative_role_view(request, assignment_id):
    """
    Revoke an active administrative role assignment (Section 10, 17, 64).
    Enforces mandatory reason and anti-self-demotion invariants.
    """
    assignment = get_object_or_404(AdministrativeAssignment, id=assignment_id, is_active=True)

    if not can_revoke_role(request.user, assignment):
        raise PermissionDenied("You are not authorized to revoke this administrative assignment or this violates safety invariants.")

    form = AdministrativeRoleRevocationForm(request.POST)
    if not form.is_valid():
        messages.error(request, "A valid revocation reason is required.")
        return redirect('role-list')

    reason = form.cleaned_data['reason']

    try:
        revoked = revoke_role(actor=request.user, assignment=assignment, reason=reason)
        messages.success(
            request,
            f"Successfully revoked {assignment.role.name} assignment from {assignment.personnel.fullname}. Their user account and service history remain intact."
        )
    except (ValidationError, PermissionDenied) as e:
        messages.error(request, str(e))

    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or reverse('role-list')
    return redirect(next_url)


@login_required(login_url='login-staff')
def role_permissions_edit_view(request, role_id):
    """
    Configure application role permissions matrix (Section 8, 41).
    Restricted to Super Admin / National Administrator.
    """
    if not (request.user.is_superuser or get_actor_administrative_scope(request.user)["level"] == "NATIONAL"):
        raise PermissionDenied("Only National Super Administrators can configure role permissions.")

    role = get_object_or_404(ApplicationRole, id=role_id)

    if request.method == 'POST':
        selected_perm_ids = request.POST.getlist('permissions')
        selected_perms = AppPermission.objects.filter(id__in=selected_perm_ids, is_active=True)

        role.permissions.set(selected_perms)
        AuditLog.objects.create(
            user=request.user,
            action="ROLE_UPDATED",
            target=f"Role: {role.code}",
            new_values={"permissions": list(selected_perms.values_list('code', flat=True))},
            reason="Role permissions reconfigured by Super Admin"
        )
        messages.success(request, f"Permissions for role '{role.name}' updated successfully.")
        return redirect('role-list')

    all_permissions = AppPermission.objects.filter(is_active=True).order_by('module', 'name')
    # Group permissions by module
    perms_by_module = {}
    for p in all_permissions:
        if hasattr(p, 'get_module_display') and callable(getattr(p, 'get_module_display', None)):
            try:
                module_name = p.get_module_display()
            except Exception:
                module_name = (p.module or '').replace('_', ' ').title() or 'General'
        else:
            module_name = (p.module or '').replace('_', ' ').title() or 'General'
        perms_by_module.setdefault(module_name, []).append(p)

    current_perm_ids = set(role.permissions.values_list('id', flat=True))

    return render(request, "gnfs_komenda/roles/role_permissions.html", {
        "role": role,
        "perms_by_module": perms_by_module,
        "current_perm_ids": current_perm_ids,
    })
