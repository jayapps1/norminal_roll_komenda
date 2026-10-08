"""
GNFS Functional Workspaces and Workflow Views.
Architectural Migration: Two simultaneous organizational dimensions:
1. Command / Geographical Structure (National -> Region -> District -> Station -> Post)
2. Functional Structure (Administration, Accounts, Operations, HR, Watch Room)
"""

from datetime import date, timedelta
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from gnfs_komenda.models import (
    AuditLog, CommandAppointment, District, FunctionalOffice,
    LeaveApprovalDecision, LeaveEntitlement, LeaveRequest, LeaveType,
    LeaveWorkflowHistory, NorminalRoll, Rank, Region, Station, WatchRoomBooking
)
from gnfs_komenda.security import (
    get_personnel_for_user, get_user_assignment, get_user_role
)
from gnfs_komenda.services.functional_scope_service import (
    get_functional_scope, get_scoped_nominal_roll,
    get_scoped_operational_personnel, get_user_authorized_workspaces
)
from gnfs_komenda.services.leave_calculator import calculate_leave_dates
from gnfs_komenda.services.leave_routing_service import (
    admin_release_to_watch_room, can_user_book_watch_room,
    can_user_final_approve_leave, can_user_process_leave,
    can_user_recommend_leave, can_user_release_to_watch_room,
    final_approve_leave_request, final_reject_leave_request,
    operations_recommend_leave_request, process_leave_request,
    submit_personnel_leave_request
)


# ==============================================================================
# 1. ACCOUNTS WORKSPACE & NOMINAL ROLL (Sections 11-18, 71-73)
# Accounts owns the Nominal Roll functional workspace/view.
# Single authoritative personnel database; scoped functional view.
# ==============================================================================

@login_required(login_url='login-staff')
def workspace_accounts_district_view(request, district_id):
    """
    District Accounts Nominal Roll Workspace (Section 12, 13, 71).
    Strictly scoped to its District. Search applies scope FIRST.
    """
    district = get_object_or_404(District.objects.select_related('region'), id=district_id)
    scope = get_functional_scope(request.user, "ACCOUNTS")

    if not scope["authorized"]:
        raise PermissionDenied("You do not have Accounts functional access.")

    # Scope Boundary Check (Section 13)
    if scope["level"] == "NATIONAL":
        pass
    elif scope["level"] == "REGIONAL":
        if not scope["region"] or scope["region"].id != district.region_id:
            raise PermissionDenied(f"Accounts scope for {scope['region']} cannot view {district.name} District.")
    elif scope["level"] == "DISTRICT":
        if not scope["district"] or scope["district"].id != district.id:
            raise PermissionDenied(f"District Accounts for {scope['district']} cannot view {district.name} District.")
    else:
        raise PermissionDenied("Unauthorized Accounts scope level.")

    # Search and Scope (Section 18)
    search_query = request.GET.get('q', '').strip()
    personnel_qs = get_scoped_nominal_roll(request.user, district_id=district.id, search_query=search_query)

    # Filter params
    rank_filter = request.GET.get('rank', '').strip()
    status_filter = request.GET.get('status', '').strip()
    if rank_filter:
        personnel_qs = personnel_qs.filter(rank__id=rank_filter)
    if status_filter:
        personnel_qs = personnel_qs.filter(leave_status=status_filter)

    # Export to Excel if requested
    if request.GET.get('export') == 'excel':
        from gnfs_komenda.views import export_personnel_excel
        return export_personnel_excel(request, queryset=personnel_qs, filename=f"NominalRoll_{district.code or district.name}.xlsx")

    # Metrics
    total_personnel = personnel_qs.count()
    at_post = personnel_qs.filter(leave_status="AT POST").count()
    on_leave = personnel_qs.filter(leave_status="ON LEAVE").count()
    payroll_active = personnel_qs.filter(is_active=True).count()
    payroll_exceptions = personnel_qs.filter(
        Q(bank__isnull=True) | Q(bank='') |
        Q(account_number__isnull=True) | Q(account_number='')
    ).count()

    paginator = Paginator(personnel_qs, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    ranks = Rank.objects.all().order_by('hierarchy_order')

    return render(request, "gnfs_komenda/workspaces/accounts_district.html", {
        "district": district,
        "region": district.region,
        "page_obj": page_obj,
        "total_personnel": total_personnel,
        "at_post": at_post,
        "on_leave": on_leave,
        "payroll_active": payroll_active,
        "payroll_exceptions": payroll_exceptions,
        "search_query": search_query,
        "rank_filter": rank_filter,
        "status_filter": status_filter,
        "ranks": ranks,
        "scope": scope,
    })


@login_required(login_url='login-staff')
def workspace_accounts_regional_view(request, region_id):
    """
    Regional Accounts Nominal Roll Workspace (Section 14, 72).
    Shows all districts under the region with drill-down capability.
    """
    region = get_object_or_404(Region, id=region_id)
    scope = get_functional_scope(request.user, "ACCOUNTS")

    if not scope["authorized"]:
        raise PermissionDenied("You do not have Accounts functional access.")

    if scope["level"] == "NATIONAL":
        pass
    elif scope["level"] == "REGIONAL":
        if not scope["region"] or scope["region"].id != region.id:
            raise PermissionDenied(f"Accounts scope for {scope['region']} cannot view {region.name} Region.")
    else:
        raise PermissionDenied("District Accounts cannot access Regional Accounts overview.")

    districts = District.objects.filter(region=region).order_by('name')

    district_summaries = []
    total_personnel = 0
    total_at_post = 0
    total_on_leave = 0
    total_exceptions = 0

    for d in districts:
        qs = NorminalRoll.active_objects.filter(
            Q(postings__district=d, postings__is_current=True) |
            Q(station__district=d)
        ).distinct()
        p_count = qs.count()
        p_at_post = qs.filter(leave_status="AT POST").count()
        p_on_leave = qs.filter(leave_status="ON LEAVE").count()
        p_exceptions = qs.filter(
            Q(bank__isnull=True) | Q(bank='') |
            Q(account_number__isnull=True) | Q(account_number='')
        ).count()

        total_personnel += p_count
        total_at_post += p_at_post
        total_on_leave += p_on_leave
        total_exceptions += p_exceptions

        district_summaries.append({
            "district": d,
            "personnel_count": p_count,
            "at_post": p_at_post,
            "on_leave": p_on_leave,
            "payroll_exceptions": p_exceptions,
        })

    return render(request, "gnfs_komenda/workspaces/accounts_regional.html", {
        "region": region,
        "districts_count": districts.count(),
        "district_summaries": district_summaries,
        "total_personnel": total_personnel,
        "at_post": total_at_post,
        "on_leave": total_on_leave,
        "payroll_exceptions": total_exceptions,
        "scope": scope,
    })


@login_required(login_url='login-staff')
def workspace_accounts_national_view(request):
    """
    National Accounts Nominal Roll Workspace (Section 15, 16, 73).
    National Directorate oversight of all regions and districts.
    """
    scope = get_functional_scope(request.user, "ACCOUNTS")

    if not scope["authorized"] or scope["level"] != "NATIONAL":
        raise PermissionDenied("National Accounts Directorate access required.")

    regions = Region.objects.all().order_by('name')

    region_summaries = []
    national_personnel = 0
    national_at_post = 0
    national_on_leave = 0
    national_exceptions = 0

    for r in regions:
        qs = NorminalRoll.active_objects.filter(
            Q(postings__region=r, postings__is_current=True) |
            Q(station__district__region=r)
        ).distinct()
        p_count = qs.count()
        p_at_post = qs.filter(leave_status="AT POST").count()
        p_on_leave = qs.filter(leave_status="ON LEAVE").count()
        p_exceptions = qs.filter(
            Q(bank__isnull=True) | Q(bank='') |
            Q(account_number__isnull=True) | Q(account_number='')
        ).count()

        national_personnel += p_count
        national_at_post += p_at_post
        national_on_leave += p_on_leave
        national_exceptions += p_exceptions

        region_summaries.append({
            "region": r,
            "districts_count": r.districts.count(),
            "personnel_count": p_count,
            "at_post": p_at_post,
            "on_leave": p_on_leave,
            "payroll_exceptions": p_exceptions,
        })

    return render(request, "gnfs_komenda/workspaces/accounts_national.html", {
        "regions_count": regions.count(),
        "region_summaries": region_summaries,
        "national_personnel": national_personnel,
        "national_at_post": national_at_post,
        "national_on_leave": national_on_leave,
        "national_exceptions": national_exceptions,
        "scope": scope,
    })


# ==============================================================================
# 2. ADMINISTRATION WORKSPACE (Sections 19-28, 66)
# ==============================================================================

@login_required(login_url='login-staff')
def workspace_admin_district_view(request, district_id):
    """
    District Administration Workspace (Section 20-24, 66).
    Personnel administration, 7 leave metric cards, days remaining calculator,
    prorated entitlements, deferred leaves, and inboxes.
    """
    district = get_object_or_404(District.objects.select_related('region'), id=district_id)
    scope = get_functional_scope(request.user, "ADMINISTRATION")

    if not scope["authorized"]:
        raise PermissionDenied("You do not have Administration functional access.")

    # Scope Boundary Check
    if scope["level"] == "NATIONAL":
        pass
    elif scope["level"] == "REGIONAL":
        if not scope["region"] or scope["region"].id != district.region_id:
            raise PermissionDenied(f"Regional Administration for {scope['region']} cannot manage {district.name} District.")
    elif scope["level"] == "DISTRICT":
        if not scope["district"] or scope["district"].id != district.id:
            raise PermissionDenied(f"District Administration for {scope['district']} cannot manage {district.name} District.")
    else:
        raise PermissionDenied("Unauthorized Administration scope.")

    NorminalRoll.sync_leaves()

    personnel_qs = NorminalRoll.active_objects.filter(
        Q(postings__district=district, postings__is_current=True) |
        Q(station__district=district)
    ).select_related('rank', 'station').distinct()

    today = timezone.localdate()
    soon_threshold = today + timedelta(days=7)

    # 8 Summary Metrics (Prompt 2 Section 5)
    total_personnel = personnel_qs.count()
    at_post = personnel_qs.filter(leave_status="AT POST").count()
    on_leave = personnel_qs.filter(leave_status="ON LEAVE").count()

    # Leave requests for this district
    district_leaves = LeaveRequest.objects.filter(
        routing_district=district
    ).select_related(
        'personnel', 'personnel__rank', 'personnel__station',
        'leave_type', 'entitlement', 'requested_by', 'approved_by'
    )

    inbox_new = district_leaves.filter(
        status__in=[
            LeaveRequest.Status.PENDING_ADMIN_REVIEW,
            LeaveRequest.Status.PENDING_PROCESSING,
            LeaveRequest.Status.SUBMITTED
        ]
    ).order_by('-created_at')

    inbox_awaiting_ops = district_leaves.filter(
        status=LeaveRequest.Status.PENDING_OPERATIONS_RECOMMENDATION
    ).order_by('-updated_at')

    inbox_awaiting_command = district_leaves.filter(
        status__in=[LeaveRequest.Status.PENDING_DFO_MFO_DECISION, LeaveRequest.Status.PENDING_APPROVAL]
    ).order_by('-updated_at')

    returning_soon = district_leaves.filter(
        status__in=[LeaveRequest.Status.ACTIVE, LeaveRequest.Status.BOOKED],
        resume_date__gte=today,
        resume_date__lte=soon_threshold
    ).count()

    deferred_count = LeaveEntitlement.objects.filter(
        personnel__in=personnel_qs,
        carried_forward__gt=0
    ).count()

    leave_requests = inbox_new.count()
    awaiting_ops = inbox_awaiting_ops.count()
    awaiting_command = inbox_awaiting_command.count()

    # Annual leave due
    current_year = today.year
    annual_leave_type = LeaveType.objects.filter(code="ANNUAL").first()
    annual_due_count = 0
    if annual_leave_type:
        annual_due_count = LeaveEntitlement.objects.filter(
            personnel__in=personnel_qs,
            leave_year=current_year,
            leave_type=annual_leave_type,
            days_taken=0
        ).count()

    inbox_returned = district_leaves.filter(
        status=LeaveRequest.Status.RETURNED_FOR_CORRECTION
    ).order_by('-updated_at')

    inbox_final_processing = district_leaves.filter(
        status__in=[
            LeaveRequest.Status.APPROVED_PENDING_ADMIN_PROCESSING,
            LeaveRequest.Status.PENDING_ADMIN_FINAL_PROCESSING,
            LeaveRequest.Status.APPROVED,
        ]
    ).order_by('-approved_at')

    inbox_sent_to_watch_room = district_leaves.filter(
        status=LeaveRequest.Status.SENT_TO_WATCH_ROOM
    ).order_by('-sent_to_watch_room_at')

    inbox_booked = district_leaves.filter(
        status=LeaveRequest.Status.BOOKED
    ).order_by('-watch_room_booked_at')

    inbox_active = district_leaves.filter(
        status=LeaveRequest.Status.ACTIVE
    ).order_by('resume_date')

    # Days Remaining Calculations (Section 22)
    active_leaves_data = []
    for lr in district_leaves.filter(status__in=[LeaveRequest.Status.ACTIVE, LeaveRequest.Status.BOOKED]):
        elapsed = 0
        remaining = 0
        if today >= lr.start_date:
            elapsed = (today - lr.start_date).days
        if lr.last_leave_date >= today:
            remaining = (lr.last_leave_date - today).days + 1
        active_leaves_data.append({
            "leave_req": lr,
            "elapsed_days": elapsed,
            "days_remaining": max(0, remaining),
            "is_working_days": lr.calculation_mode == LeaveType.CalculationMode.WORKING_DAYS
        })

    # Prorated Entitlements (Section 23)
    prorated_entitlements = LeaveEntitlement.objects.filter(
        personnel__in=personnel_qs,
        leave_year=current_year,
        is_prorated=True
    ).select_related('personnel', 'leave_type')

    # Administration Personnel Information (Prompt 2 Section 6)
    entitlements_map = {
        e.personnel_id: e for e in LeaveEntitlement.objects.filter(
            personnel__in=personnel_qs,
            leave_year=current_year
        ).select_related('leave_type')
    }

    upcoming_leaves_map = {}
    for lr in district_leaves.filter(
        status__in=[LeaveRequest.Status.APPROVED, LeaveRequest.Status.BOOKED],
        start_date__gte=today
    ).order_by('start_date'):
        if lr.personnel_id not in upcoming_leaves_map:
            upcoming_leaves_map[lr.personnel_id] = lr

    personnel_roster = []
    for p in personnel_qs.prefetch_related('functional_assignments', 'postings'):
        ent = entitlements_map.get(p.id)
        up_leave = upcoming_leaves_map.get(p.id)
        is_ret_soon = False
        if p.leave_status == "ON LEAVE" and p.end_leave and today <= p.end_leave <= soon_threshold:
            is_ret_soon = True

        personnel_roster.append({
            "personnel": p,
            "rank": p.rank.abbreviation if p.rank else "-",
            "officer_category": getattr(p, 'get_officer_category_display', lambda: p.officer_category)(),
            "station": p.station.name if p.station else "-",
            "functional_assignment": p.primary_functional_assignment.functional_unit.name if p.primary_functional_assignment and p.primary_functional_assignment.functional_unit else "-",
            "leave_status": p.leave_status,
            "total_entitlement": ent.total_entitlement if ent else 0,
            "days_taken": ent.days_taken if ent else 0,
            "days_remaining": ent.remaining_balance if ent else 0,
            "deferred_leave": ent.carried_forward if ent else 0,
            "is_prorated": ent.is_prorated if ent else False,
            "prorated_days": ent.prorated_days if ent and ent.is_prorated else None,
            "upcoming_leave": up_leave,
            "returning_soon": is_ret_soon,
        })

    return render(request, "gnfs_komenda/workspaces/admin_district.html", {
        "district": district,
        "region": district.region,
        "total_personnel": total_personnel,
        "at_post": at_post,
        "on_leave": on_leave,
        "leave_requests": leave_requests,
        "leave_pending": leave_requests,
        "awaiting_ops": awaiting_ops,
        "awaiting_command": awaiting_command,
        "returning_soon": returning_soon,
        "deferred_count": deferred_count,
        "annual_due_count": annual_due_count,
        "inbox_new": inbox_new,
        "inbox_returned": inbox_returned,
        "inbox_awaiting_ops": inbox_awaiting_ops,
        "inbox_awaiting_command": inbox_awaiting_command,
        "inbox_final_processing": inbox_final_processing,
        "inbox_sent_to_watch_room": inbox_sent_to_watch_room,
        "inbox_booked": inbox_booked,
        "inbox_active": inbox_active,
        "active_leaves_data": active_leaves_data,
        "prorated_entitlements": prorated_entitlements,
        "personnel_roster": personnel_roster,
        "scope": scope,
    })


@login_required(login_url='login-staff')
def workspace_admin_regional_view(request, region_id):
    """
    Regional Administration Workspace (Section 25-26).
    Aggregates District Administration status across all districts in the Region.
    """
    region = get_object_or_404(Region, id=region_id)
    scope = get_functional_scope(request.user, "ADMINISTRATION")

    if not scope["authorized"]:
        raise PermissionDenied("You do not have Administration functional access.")

    if scope["level"] == "NATIONAL":
        pass
    elif scope["level"] == "REGIONAL":
        if not scope["region"] or scope["region"].id != region.id:
            raise PermissionDenied(f"Regional Administration for {scope['region']} cannot view {region.name} Region.")
    else:
        raise PermissionDenied("District Administration cannot access Regional Administration overview.")

    districts = District.objects.filter(region=region).order_by('name')

    district_summaries = []
    total_personnel = 0
    total_at_post = 0
    total_on_leave = 0
    total_pending = 0
    total_deferred = 0

    today = timezone.localdate()

    for d in districts:
        qs = NorminalRoll.active_objects.filter(
            Q(postings__district=d, postings__is_current=True) |
            Q(station__district=d)
        ).distinct()
        p_count = qs.count()
        p_at_post = qs.filter(leave_status="AT POST").count()
        p_on_leave = qs.filter(leave_status="ON LEAVE").count()

        p_pending = LeaveRequest.objects.filter(
            routing_district=d,
            status__in=[
                LeaveRequest.Status.PENDING_ADMIN_REVIEW,
                LeaveRequest.Status.PENDING_PROCESSING,
                LeaveRequest.Status.PENDING_OPERATIONS_RECOMMENDATION,
                LeaveRequest.Status.PENDING_APPROVAL
            ]
        ).count()

        p_deferred = LeaveEntitlement.objects.filter(
            personnel__in=qs,
            carried_forward__gt=0
        ).count()

        total_personnel += p_count
        total_at_post += p_at_post
        total_on_leave += p_on_leave
        total_pending += p_pending
        total_deferred += p_deferred

        district_summaries.append({
            "district": d,
            "personnel_count": p_count,
            "at_post": p_at_post,
            "on_leave": p_on_leave,
            "pending_count": p_pending,
            "deferred_count": p_deferred,
        })

    return render(request, "gnfs_komenda/workspaces/admin_regional.html", {
        "region": region,
        "districts_count": districts.count(),
        "district_summaries": district_summaries,
        "total_personnel": total_personnel,
        "at_post": total_at_post,
        "on_leave": total_on_leave,
        "pending_count": total_pending,
        "deferred_count": total_deferred,
        "scope": scope,
    })


@login_required(login_url='login-staff')
def workspace_admin_national_view(request):
    """
    National Administration Directorate Workspace (Section 27-28).
    Nationwide aggregation across all regions with drill-down capability.
    """
    scope = get_functional_scope(request.user, "ADMINISTRATION")

    if not scope["authorized"] or scope["level"] != "NATIONAL":
        raise PermissionDenied("National Administration Directorate access required.")

    regions = Region.objects.all().order_by('name')

    region_summaries = []
    nat_personnel = 0
    nat_at_post = 0
    nat_on_leave = 0
    nat_pending = 0

    for r in regions:
        qs = NorminalRoll.active_objects.filter(
            Q(postings__region=r, postings__is_current=True) |
            Q(station__district__region=r)
        ).distinct()
        p_count = qs.count()
        p_at_post = qs.filter(leave_status="AT POST").count()
        p_on_leave = qs.filter(leave_status="ON LEAVE").count()

        p_pending = LeaveRequest.objects.filter(
            routing_region=r,
            status__in=[
                LeaveRequest.Status.PENDING_ADMIN_REVIEW,
                LeaveRequest.Status.PENDING_PROCESSING,
                LeaveRequest.Status.PENDING_OPERATIONS_RECOMMENDATION,
                LeaveRequest.Status.PENDING_APPROVAL
            ]
        ).count()

        nat_personnel += p_count
        nat_at_post += p_at_post
        nat_on_leave += p_on_leave
        nat_pending += p_pending

        region_summaries.append({
            "region": r,
            "districts_count": r.districts.count(),
            "personnel_count": p_count,
            "at_post": p_at_post,
            "on_leave": p_on_leave,
            "pending_count": p_pending,
        })

    return render(request, "gnfs_komenda/workspaces/admin_national.html", {
        "regions_count": regions.count(),
        "region_summaries": region_summaries,
        "national_personnel": nat_personnel,
        "national_at_post": nat_at_post,
        "national_on_leave": nat_on_leave,
        "national_pending": nat_pending,
        "scope": scope,
    })


# ==============================================================================
# 3. OPERATIONS WORKSPACE (Sections 29-35, 67)
# ==============================================================================

@login_required(login_url='login-staff')
def workspace_ops_district_view(request, district_id):
    """
    District Operations Workspace (Prompt 2 Section 8, 9).
    Focuses on operational personnel availability, station strength, and recommendations.
    Operations sees ONLY Personnel within its authorised Operations functional scope.
    """
    district = get_object_or_404(District.objects.select_related('region'), id=district_id)
    scope = get_functional_scope(request.user, "OPERATIONS")

    if not scope["authorized"]:
        raise PermissionDenied("You do not have Operations functional access.")

    # Scope Boundary Check
    if scope["level"] == "NATIONAL":
        pass
    elif scope["level"] == "REGIONAL":
        if not scope["region"] or scope["region"].id != district.region_id:
            raise PermissionDenied(f"Regional Operations for {scope['region']} cannot view {district.name} District.")
    elif scope["level"] == "DISTRICT":
        if not scope["district"] or scope["district"].id != district.id:
            raise PermissionDenied(f"District Operations for {scope['district']} cannot view {district.name} District.")
    else:
        raise PermissionDenied("Unauthorized Operations scope.")

    NorminalRoll.sync_leaves()

    # Scoped operational personnel (Prompt 2 Section 8)
    ops_personnel = get_scoped_operational_personnel(request.user, district_id=district.id)

    today = timezone.localdate()
    soon_threshold = today + timedelta(days=7)

    # 5 Summary Metrics (Prompt 2 Section 8)
    total_ops = ops_personnel.count()
    at_post = ops_personnel.filter(leave_status="AT POST").count()
    on_leave = ops_personnel.filter(leave_status="ON LEAVE").count()
    unavailable = ops_personnel.filter(
        leave_status="ON LEAVE",
        leave_type__in=["SICK LEAVE", "COMPASSIONATE LEAVE", "MATERNITY LEAVE"]
    ).count()

    returning_soon = ops_personnel.filter(
        leave_status="ON LEAVE",
        end_leave__gte=today,
        end_leave__lte=soon_threshold
    ).count()

    # Station & Post Operational Strength Breakdown (Section 30)
    stations = Station.objects.filter(district=district).order_by('name')
    station_strength = []
    for s in stations:
        stn_personnel = ops_personnel.filter(station=s)
        stn_avail = stn_personnel.filter(leave_status="AT POST").count()
        stn_total = stn_personnel.count()
        station_strength.append({
            "station": s,
            "total": stn_total,
            "available": stn_avail,
            "on_leave": stn_total - stn_avail,
        })

    # Operations Inboxes (Section 67)
    district_leaves = LeaveRequest.objects.filter(
        routing_district=district
    ).select_related('personnel', 'leave_type', 'entitlement', 'requested_by')

    inbox_awaiting_recommendation = district_leaves.filter(
        status=LeaveRequest.Status.PENDING_OPERATIONS_RECOMMENDATION
    ).order_by('-updated_at')
    awaiting_rec_count = inbox_awaiting_recommendation.count()

    inbox_recommended = district_leaves.filter(
        operations_recommendation="RECOMMEND"
    ).order_by('-operations_recommended_at')[:20]

    inbox_not_recommended = district_leaves.filter(
        operations_recommendation="NOT_RECOMMEND"
    ).order_by('-operations_recommended_at')[:20]

    inbox_on_leave = ops_personnel.filter(leave_status="ON LEAVE").order_by('end_leave')

    # Operations Personnel Roster (Prompt 2 Section 9)
    active_ops_leaves_map = {
        lr.personnel_id: lr for lr in district_leaves.filter(
            status__in=[LeaveRequest.Status.ACTIVE, LeaveRequest.Status.BOOKED]
        )
    }
    upcoming_ops_leaves_map = {}
    for lr in district_leaves.filter(
        status__in=[LeaveRequest.Status.APPROVED, LeaveRequest.Status.BOOKED],
        start_date__gte=today
    ).order_by('start_date'):
        if lr.personnel_id not in upcoming_ops_leaves_map:
            upcoming_ops_leaves_map[lr.personnel_id] = lr

    ops_roster = []
    for p in ops_personnel.select_related('rank', 'station'):
        cur_leave = active_ops_leaves_map.get(p.id)
        up_leave = upcoming_ops_leaves_map.get(p.id)
        days_rem = 0
        if cur_leave and cur_leave.last_leave_date and cur_leave.last_leave_date >= today:
            days_rem = (cur_leave.last_leave_date - today).days + 1

        ops_roster.append({
            "personnel": p,
            "staffID": p.staffID,
            "name": p.fullname,
            "rank": p.rank.abbreviation if p.rank else "-",
            "station": p.station.name if p.station else "-",
            "watch": getattr(p, 'duty_shift', None) or getattr(p, 'watch', None) or "Duty Watch",
            "leave_status": p.leave_status,
            "leave_type": cur_leave.leave_type.name if cur_leave else "-",
            "leave_start": cur_leave.start_date if cur_leave else None,
            "resume_date": cur_leave.resume_date if cur_leave else None,
            "days_remaining": days_rem,
            "upcoming_leave": up_leave,
        })

    return render(request, "gnfs_komenda/workspaces/ops_district.html", {
        "district": district,
        "region": district.region,
        "total_ops": total_ops,
        "at_post": at_post,
        "on_leave": on_leave,
        "unavailable": unavailable,
        "returning_soon": returning_soon,
        "awaiting_rec_count": awaiting_rec_count,
        "station_strength": station_strength,
        "inbox_awaiting_rec": inbox_awaiting_recommendation,
        "inbox_recommended": inbox_recommended,
        "inbox_not_recommended": inbox_not_recommended,
        "inbox_on_leave": inbox_on_leave,
        "ops_roster": ops_roster,
        "scope": scope,
    })


@login_required(login_url='login-staff')
def workspace_ops_regional_view(request, region_id):
    """
    Regional Operations Workspace (Section 33).
    Operational personnel visibility across all districts in the region.
    """
    region = get_object_or_404(Region, id=region_id)
    scope = get_functional_scope(request.user, "OPERATIONS")

    if not scope["authorized"]:
        raise PermissionDenied("You do not have Operations functional access.")

    if scope["level"] == "NATIONAL":
        pass
    elif scope["level"] == "REGIONAL":
        if not scope["region"] or scope["region"].id != region.id:
            raise PermissionDenied(f"Regional Operations for {scope['region']} cannot view {region.name} Region.")
    else:
        raise PermissionDenied("District Operations cannot access Regional Operations overview.")

    districts = District.objects.filter(region=region).order_by('name')

    district_summaries = []
    total_ops = 0
    total_at_post = 0
    total_on_leave = 0
    total_awaiting_rec = 0

    for d in districts:
        qs = NorminalRoll.active_objects.filter(
            Q(postings__district=d, postings__is_current=True) |
            Q(station__district=d)
        ).distinct()
        ops_qs = qs.filter(
            Q(functional_assignments__functional_unit__name__icontains="Operations") |
            Q(functional_assignments__functional_office__function_type="OPERATIONS")
        ).distinct()
        if not ops_qs.exists():
            ops_qs = qs

        p_count = ops_qs.count()
        p_at_post = ops_qs.filter(leave_status="AT POST").count()
        p_on_leave = ops_qs.filter(leave_status="ON LEAVE").count()

        p_awaiting = LeaveRequest.objects.filter(
            routing_district=d,
            status=LeaveRequest.Status.PENDING_OPERATIONS_RECOMMENDATION
        ).count()

        total_ops += p_count
        total_at_post += p_at_post
        total_on_leave += p_on_leave
        total_awaiting_rec += p_awaiting

        district_summaries.append({
            "district": d,
            "ops_count": p_count,
            "at_post": p_at_post,
            "on_leave": p_on_leave,
            "awaiting_rec": p_awaiting,
        })

    return render(request, "gnfs_komenda/workspaces/ops_regional.html", {
        "region": region,
        "districts_count": districts.count(),
        "district_summaries": district_summaries,
        "total_ops": total_ops,
        "at_post": total_at_post,
        "on_leave": total_on_leave,
        "awaiting_rec_total": total_awaiting_rec,
        "scope": scope,
    })


@login_required(login_url='login-staff')
def workspace_ops_national_view(request):
    """
    National Operations Directorate Workspace (Section 34, 35).
    National operational overview across all regions.
    """
    scope = get_functional_scope(request.user, "OPERATIONS")

    if not scope["authorized"] or scope["level"] != "NATIONAL":
        raise PermissionDenied("National Operations Directorate access required.")

    regions = Region.objects.all().order_by('name')

    region_summaries = []
    nat_ops = 0
    nat_at_post = 0
    nat_on_leave = 0

    for r in regions:
        qs = NorminalRoll.active_objects.filter(
            Q(postings__region=r, postings__is_current=True) |
            Q(station__district__region=r)
        ).distinct()
        ops_qs = qs.filter(
            Q(functional_assignments__functional_unit__name__icontains="Operations") |
            Q(functional_assignments__functional_office__function_type="OPERATIONS")
        ).distinct()
        if not ops_qs.exists():
            ops_qs = qs

        p_count = ops_qs.count()
        p_at_post = ops_qs.filter(leave_status="AT POST").count()
        p_on_leave = ops_qs.filter(leave_status="ON LEAVE").count()

        nat_ops += p_count
        nat_at_post += p_at_post
        nat_on_leave += p_on_leave

        region_summaries.append({
            "region": r,
            "districts_count": r.districts.count(),
            "ops_count": p_count,
            "at_post": p_at_post,
            "on_leave": p_on_leave,
        })

    return render(request, "gnfs_komenda/workspaces/ops_national.html", {
        "regions_count": regions.count(),
        "region_summaries": region_summaries,
        "national_ops": nat_ops,
        "national_at_post": nat_at_post,
        "national_on_leave": nat_on_leave,
        "scope": scope,
    })


# ==============================================================================
# 4. HUMAN RESOURCE WORKSPACE (Sections 36-39)
# Seamless fallback to Administration where HR is not established (e.g. KEEA).
# ==============================================================================

@login_required(login_url='login-staff')
def workspace_hr_district_view(request, district_id):
    """
    District HR Workspace (Section 36, 37).
    Where HR is unestablished, seamlessly displays delegated HR functions to Administration.
    """
    district = get_object_or_404(District.objects.select_related('region'), id=district_id)
    scope = get_functional_scope(request.user, "HR")

    if not scope["authorized"]:
        raise PermissionDenied("You do not have Human Resource functional access.")

    # Check whether District HR is established (Section 37)
    hr_office = FunctionalOffice.objects.filter(
        district=district,
        function_type=FunctionalOffice.FunctionType.HR
    ).first()

    is_fallback = False
    fallback_note = ""
    if hr_office and not hr_office.is_established:
        is_fallback = True
        fallback_target = hr_office.fallback_office.name if hr_office.fallback_office else "District Administration"
        fallback_note = f"Dedicated District HR Office is not established for {district.name}. HR responsibilities are performed by {fallback_target}."

    personnel_qs = NorminalRoll.active_objects.filter(
        Q(postings__district=district, postings__is_current=True) |
        Q(station__district=district)
    ).select_related('rank', 'station').distinct()

    total_personnel = personnel_qs.count()
    senior_officers = personnel_qs.filter(officer_category=Rank.OfficerCategory.SENIOR).count()
    junior_officers = personnel_qs.filter(officer_category=Rank.OfficerCategory.JUNIOR).count()

    rank_distribution = personnel_qs.values(
        'rank__name', 'rank__abbreviation'
    ).annotate(count=Count('id')).order_by('-count')

    # Postings & Transfers
    stations_count = Station.objects.filter(district=district).count()

    return render(request, "gnfs_komenda/workspaces/hr_district.html", {
        "district": district,
        "region": district.region,
        "total_personnel": total_personnel,
        "senior_officers": senior_officers,
        "junior_officers": junior_officers,
        "rank_distribution": rank_distribution,
        "stations_count": stations_count,
        "is_fallback": is_fallback,
        "fallback_note": fallback_note,
        "scope": scope,
    })


# ==============================================================================
# 5. WATCH ROOM WORKSPACE (Sections 40-43, 69, 70)
# District-level operational recording / Station Diary booking.
# Least privilege: No sensitive banking or private HR data.
# ==============================================================================

@login_required(login_url='login-staff')
def workspace_watch_room_district_view(request, district_id):
    """
    District Watch Room Workspace (Section 40-43, 69).
    Operational record booking in Station Diary. Cannot alter leave dates/duration.
    """
    district = get_object_or_404(District.objects.select_related('region'), id=district_id)
    scope = get_functional_scope(request.user, "WATCH_ROOM")

    if not scope["authorized"]:
        raise PermissionDenied("You do not have Watch Room functional access.")

    # Scope Boundary Check
    if scope["level"] == "NATIONAL":
        pass
    elif scope["level"] == "REGIONAL":
        if not scope["region"] or scope["region"].id != district.region_id:
            raise PermissionDenied(f"Watch Room scope does not cover {district.name} District.")
    elif scope["level"] == "DISTRICT":
        if not scope["district"] or scope["district"].id != district.id:
            raise PermissionDenied(f"Watch Room scope for {scope['district']} cannot view {district.name} District.")
    else:
        raise PermissionDenied("Unauthorized Watch Room scope.")

    today = timezone.localdate()
    tomorrow = today + timedelta(days=1)

    district_leaves = LeaveRequest.objects.filter(
        routing_district=district
    ).select_related('personnel', 'personnel__rank', 'personnel__station', 'leave_type', 'approved_by')

    # Inboxes (Section 69)
    inbox_awaiting_booking = district_leaves.filter(
        status=LeaveRequest.Status.SENT_TO_WATCH_ROOM
    ).order_by('-sent_to_watch_room_at')

    inbox_booked_today = district_leaves.filter(
        watch_room_booked_at__date=today
    ).order_by('-watch_room_booked_at')

    inbox_on_leave = district_leaves.filter(
        status__in=[LeaveRequest.Status.ACTIVE, LeaveRequest.Status.BOOKED]
    ).order_by('resume_date')

    inbox_returning_today = district_leaves.filter(
        status__in=[LeaveRequest.Status.ACTIVE, LeaveRequest.Status.BOOKED],
        resume_date=today
    )

    inbox_returning_tomorrow = district_leaves.filter(
        status__in=[LeaveRequest.Status.ACTIVE, LeaveRequest.Status.BOOKED],
        resume_date=tomorrow
    )

    inbox_overdue = district_leaves.filter(
        status=LeaveRequest.Status.ACTIVE,
        last_leave_date__lt=today
    )

    return render(request, "gnfs_komenda/workspaces/watch_room_district.html", {
        "district": district,
        "region": district.region,
        "awaiting_booking_count": inbox_awaiting_booking.count(),
        "booked_today_count": inbox_booked_today.count(),
        "currently_on_leave_count": inbox_on_leave.count(),
        "returning_today_count": inbox_returning_today.count(),
        "returning_tomorrow_count": inbox_returning_tomorrow.count(),
        "overdue_count": inbox_overdue.count(),
        "inbox_awaiting_booking": inbox_awaiting_booking,
        "inbox_booked_today": inbox_booked_today,
        "inbox_on_leave": inbox_on_leave,
        "inbox_returning_today": inbox_returning_today,
        "inbox_returning_tomorrow": inbox_returning_tomorrow,
        "inbox_overdue": inbox_overdue,
        "scope": scope,
    })


# ==============================================================================
# 6. WORKFLOW ACTION ENDPOINTS
# ==============================================================================

@login_required(login_url='login-staff')
def admin_process_leave_action(request, leave_req_id):
    """
    Administration processing action: forward to Operations or return for correction.
    """
    if request.method != "POST":
        return redirect("leave-detail", pk=leave_req_id)

    action = request.POST.get("action", "").strip()
    comments = request.POST.get("comments", "").strip()

    try:
        leave_req = process_leave_request(leave_req_id, request.user, action, comments=comments)
        if action in ("FORWARD", "FORWARD_TO_OPERATIONS"):
            messages.success(request, f"Leave request #{leave_req.id} verified and forwarded to Operations.")
        else:
            messages.warning(request, f"Leave request #{leave_req.id} returned for correction.")
    except PermissionDenied as e:
        messages.error(request, str(e))
    except ValidationError as e:
        messages.error(request, str(e))

    return redirect(request.META.get('HTTP_REFERER', 'personnel'))


@login_required(login_url='login-staff')
def operations_recommend_leave_action(request, leave_req_id):
    """
    Operations recommendation action: RECOMMEND, NOT_RECOMMEND, or RETURN_FOR_CORRECTION.
    """
    if request.method != "POST":
        return redirect("leave-detail", pk=leave_req_id)

    recommendation = request.POST.get("recommendation", "").strip()
    comments = request.POST.get("comments", "").strip()

    try:
        leave_req = operations_recommend_leave_request(
            leave_req_id, request.user, recommendation, comments=comments
        )
        if recommendation == "RECOMMEND":
            messages.success(request, f"Operations RECOMMENDED leave request #{leave_req.id}. Forwarded to Command.")
        elif recommendation == "NOT_RECOMMEND":
            messages.warning(request, f"Operations recorded NOT RECOMMENDED for request #{leave_req.id}. Forwarded to Command.")
        else:
            messages.info(request, f"Leave request #{leave_req.id} returned for correction by Operations.")
    except PermissionDenied as e:
        messages.error(request, str(e))
    except ValidationError as e:
        messages.error(request, str(e))

    return redirect(request.META.get('HTTP_REFERER', 'personnel'))


@login_required(login_url='login-staff')
def command_approve_leave_action(request, leave_req_id):
    """
    Command Decision Action (DFO / MFO / RFO): APPROVE or REJECT.
    """
    if request.method != "POST":
        return redirect("leave-detail", pk=leave_req_id)

    decision = request.POST.get("decision", "").strip()
    comments = request.POST.get("comments", "").strip()

    try:
        if decision == "APPROVE":
            leave_req = final_approve_leave_request(leave_req_id, request.user, comments=comments)
            messages.success(request, f"Leave request #{leave_req.id} command approved. Dispatched to Administration for final processing.")
        elif decision == "REJECT":
            leave_req = final_reject_leave_request(leave_req_id, request.user, comments=comments)
            messages.warning(request, f"Leave request #{leave_req.id} rejected by command.")
        else:
            messages.error(request, f"Invalid command decision '{decision}'.")
    except PermissionDenied as e:
        messages.error(request, str(e))
    except ValidationError as e:
        messages.error(request, str(e))

    return redirect(request.META.get('HTTP_REFERER', 'personnel'))


@login_required(login_url='login-staff')
def admin_release_to_watch_room_action(request, leave_req_id):
    """
    Administration releases approved leave to Watch Room.
    """
    if request.method != "POST":
        return redirect("leave-detail", pk=leave_req_id)

    comments = request.POST.get("comments", "").strip()

    try:
        leave_req = admin_release_to_watch_room(leave_req_id, request.user, comments=comments)
        messages.success(request, f"Approved leave #{leave_req.id} dispatched to Watch Room for Station Diary entry.")
    except PermissionDenied as e:
        messages.error(request, str(e))
    except ValidationError as e:
        messages.error(request, str(e))

    return redirect(request.META.get('HTTP_REFERER', 'personnel'))


@login_required(login_url='login-staff')
def watch_room_book_leave_action(request, leave_req_id):
    """
    Watch Room logs approved leave in Station Diary.
    """
    if request.method != "POST":
        return redirect("leave-detail", pk=leave_req_id)

    book_type = request.POST.get("book_type", "STATION_DIARY").strip()
    book_reference = request.POST.get("book_reference", "").strip()
    remarks = request.POST.get("remarks", "").strip()

    from gnfs_komenda.services.leave_routing_service import watch_room_book_leave
    try:
        leave_req = watch_room_book_leave(
            leave_req_id, request.user,
            book_type=book_type,
            book_reference=book_reference,
            remarks=remarks
        )
        messages.success(request, f"Leave #{leave_req.id} logged in {book_type} (Ref: {book_reference}). Status: {leave_req.get_status_display()}.")
    except PermissionDenied as e:
        messages.error(request, str(e))
    except ValidationError as e:
        messages.error(request, str(e))

    return redirect(request.META.get('HTTP_REFERER', 'personnel'))


@login_required(login_url='login-staff')
def watch_room_record_resumption_action(request, leave_req_id):
    """
    Watch Room records official resumption / return to post (Prompt 3 Section 24).
    """
    if request.method != "POST":
        return redirect("leave-detail", pk=leave_req_id)

    remarks = request.POST.get("remarks", "").strip()

    from gnfs_komenda.services.watch_room_service import watch_room_record_resumption
    try:
        leave_req = watch_room_record_resumption(leave_req_id, request.user, remarks=remarks)
        messages.success(request, f"Resumption of duty recorded for {leave_req.personnel.fullname}. Officer is now marked AT POST.")
    except PermissionDenied as e:
        messages.error(request, str(e))
    except ValidationError as e:
        messages.error(request, str(e))

    return redirect(request.META.get('HTTP_REFERER', 'personnel'))


@login_required(login_url='login-staff')
def admin_create_leave_view(request, district_id):
    """
    Administration-initiated leave for a personnel member (Section 46).
    Records created_by_admin / creation_mode='ADMIN'.
    """
    district = get_object_or_404(District, id=district_id)
    scope = get_functional_scope(request.user, "ADMINISTRATION")

    if not scope["authorized"]:
        raise PermissionDenied("You do not have Administration functional access.")

    personnel_qs = NorminalRoll.active_objects.filter(
        Q(postings__district=district, postings__is_current=True) |
        Q(station__district=district)
    ).order_by('fullname').distinct()

    leave_types = LeaveType.objects.all().order_by('name')

    if request.method == "POST":
        personnel_id = request.POST.get("personnel_id")
        leave_type_id = request.POST.get("leave_type_id")
        start_date_str = request.POST.get("start_date")
        duration_str = request.POST.get("duration")
        reason = request.POST.get("reason", "")
        contact_phone = request.POST.get("contact_phone", "")
        contact_address = request.POST.get("contact_address", "")
        is_prorated = request.POST.get("is_prorated") == "on"

        target_personnel = get_object_or_404(NorminalRoll, id=personnel_id)
        target_leave_type = get_object_or_404(LeaveType, id=leave_type_id)

        try:
            start_date = date.fromisoformat(start_date_str)
            duration = int(duration_str)
            leave_req = submit_personnel_leave_request(
                personnel=target_personnel,
                leave_type=target_leave_type,
                start_date=start_date,
                duration=duration,
                requested_by=request.user,
                reason=reason,
                contact_phone=contact_phone,
                contact_address=contact_address,
                is_proportionate=is_prorated,
                is_admin_created=True
            )
            messages.success(request, f"Leave request #{leave_req.id} created by Administration for {target_personnel.fullname}. Awaiting review.")
            return redirect("workspace-admin-district", district_id=district.id)
        except (ValueError, ValidationError) as e:
            messages.error(request, str(e))

    return render(request, "gnfs_komenda/workspaces/admin_create_leave.html", {
        "district": district,
        "personnel_list": personnel_qs,
        "leave_types": leave_types,
    })
