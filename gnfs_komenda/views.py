from datetime import datetime
import json
import openpyxl
import pandas as pd

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.timezone import localtime, now
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from gnfs_komenda.forms import (
    ApprovalDecisionForm, ArchiveRequestForm, NorminalRollForm,
    RegionForm, DistrictForm, StationForm, PostForm
)
from gnfs_komenda.models import (
    ApprovalHistory, AuditLog, District, Notification, NorminalRoll,
    PersonnelActionRequest, Post, Rank, Region, Station
)
from gnfs_komenda.security import (
    can_user_approve_request, can_user_manage_personnel,
    get_districts_queryset_for_user, get_personnel_queryset_for_user,
    get_posts_queryset_for_user, get_regions_queryset_for_user,
    get_stations_queryset_for_user, get_user_assignment, get_user_role
)
from gnfs_komenda.workflow import (
    create_archive_request, process_approval_decision, record_audit_log
)


# ==============================================================================
# LEAVE ROSTER VIEWS (PRESERVED & SCOPED)
# ==============================================================================

@login_required(login_url='login-staff')
def officers_on_leave(request):
    """View officers currently on active leave within user's authorized scope."""
    NorminalRoll.sync_leaves()
    today = timezone.now().date()
    scoped_qs = get_personnel_queryset_for_user(request.user)

    on_leave = scoped_qs.filter(
        leave_status="ON LEAVE",
        start_leave__lte=today,
        end_leave__gte=today
    )

    return render(request, "gnfs_komenda/officers_on_leave.html", {
        "officers": on_leave,
        "today": today
    })


@login_required(login_url='login-staff')
def officers_at_post(request):
    """View officers actively at post within user's authorized scope."""
    NorminalRoll.sync_leaves()
    today = timezone.now().date()
    scoped_qs = get_personnel_queryset_for_user(request.user)

    at_post = scoped_qs.exclude(
        id__in=scoped_qs.filter(
            leave_status="ON LEAVE",
            start_leave__lte=today,
            end_leave__gte=today
        ).values_list('id', flat=True)
    )

    return render(request, "gnfs_komenda/officers_at_post.html", {
        "officers": at_post
    })


# ==============================================================================
# PERSONNEL ROSTER & CRUD VIEWS
# ==============================================================================

class AllPersonnel(LoginRequiredMixin, ListView):
    model = NorminalRoll
    template_name = 'gnfs_komenda/index.html'
    context_object_name = 'staffs'
    login_url = 'login-staff'
    paginate_by = 25
    ordering = ['fullname']

    def get_queryset(self):
        NorminalRoll.sync_leaves()
        queryset = get_personnel_queryset_for_user(self.request.user)

        # Keyword Search
        q = self.request.GET.get("q")
        if q:
            q = q.strip()
            queryset = queryset.filter(
                Q(fullname__icontains=q) |
                Q(staffID__icontains=q) |
                Q(job_grade__icontains=q) |
                Q(rank__name__icontains=q) |
                Q(rank__abbreviation__icontains=q) |
                Q(ghana_card__icontains=q) |
                Q(station__name__icontains=q) |
                Q(station__district__name__icontains=q) |
                Q(station__district__region__name__icontains=q) |
                Q(post__name__icontains=q)
            )

        # Officer Category Filter
        category = self.request.GET.get("category", "").strip().upper()
        if category in (Rank.OfficerCategory.SENIOR, Rank.OfficerCategory.JUNIOR):
            queryset = queryset.filter(officer_category=category)

        # Rank Filter
        rank_id = self.request.GET.get("rank", "").strip()
        if rank_id and rank_id.isdigit():
            queryset = queryset.filter(rank_id=int(rank_id))

        return queryset

    def get_context_data(self, **kwargs):
        NorminalRoll.sync_leaves()
        context = super().get_context_data(**kwargs)
        base_qs = get_personnel_queryset_for_user(self.request.user)

        selected_cat = self.request.GET.get("category", "").strip().upper()
        selected_rank = self.request.GET.get("rank", "").strip()

        context['total_staff'] = base_qs.count()
        context['staff_on_leave'] = base_qs.filter(leave_status='ON LEAVE').count()
        context['staff_at_post'] = base_qs.filter(leave_status='AT POST').count()
        context['senior_staff_count'] = base_qs.filter(officer_category=Rank.OfficerCategory.SENIOR).count()
        context['junior_staff_count'] = base_qs.filter(officer_category=Rank.OfficerCategory.JUNIOR).count()

        context['selected_category'] = selected_cat
        context['selected_rank'] = selected_rank

        # Filter ranks dropdown options based on selected category if provided
        rank_qs = Rank.objects.filter(active=True)
        if selected_cat in (Rank.OfficerCategory.SENIOR, Rank.OfficerCategory.JUNIOR):
            rank_qs = rank_qs.filter(officer_category=selected_cat)
        context['filter_ranks'] = rank_qs.order_by('hierarchy_order')

        context['user_role'] = get_user_role(self.request.user)
        context['user_assignment'] = get_user_assignment(self.request.user)
        return context


class AddStaff(LoginRequiredMixin, CreateView):
    model = NorminalRoll
    form_class = NorminalRollForm
    template_name = 'gnfs_komenda/create-staff.html'
    success_url = reverse_lazy('personnel')
    login_url = 'login-staff'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        staff = form.save(commit=False)
        staff.staffID = staff.staffID.upper().strip()
        staff.fullname = staff.fullname.upper().strip()
        staff.job_grade = staff.job_grade.upper().strip()
        staff.ghana_card = staff.ghana_card.upper().strip()
        staff.mgmt_units = staff.mgmt_units.upper().strip() if staff.mgmt_units else ''
        staff.bank = staff.bank.upper().strip() if staff.bank else ''
        staff.account_number = staff.account_number.upper().strip() if staff.account_number else ''
        staff.is_active = True
        staff.save()

        record_audit_log(
            user=self.request.user,
            action="PERSONNEL_CREATED",
            target_type="NorminalRoll",
            target_id=staff.staffID,
            description=f"Created personnel record {staff.fullname} ({staff.staffID}) assigned to {staff.station.name if staff.station else 'None'}.",
            region=staff.current_region,
            district=staff.current_district,
            station=staff.station,
            post=staff.post,
            ip_address=self.request.META.get('REMOTE_ADDR')
        )

        messages.success(self.request, f"Personnel {staff.fullname} added successfully.")
        return redirect('personnel')


class EditStaff(LoginRequiredMixin, UpdateView):
    model = NorminalRoll
    form_class = NorminalRollForm
    template_name = 'gnfs_komenda/edit-staff.html'
    success_url = reverse_lazy('personnel')
    slug_field = 'staffID'
    slug_url_kwarg = 'staffID'
    login_url = 'login-staff'

    def get_object(self, queryset=None):
        staffID = self.kwargs.get('staffID')
        staff = get_object_or_404(NorminalRoll, staffID=staffID)
        if not can_user_manage_personnel(self.request.user, staff):
            raise PermissionDenied("You are not authorized to edit this personnel record.")
        return staff

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        staff = form.save(commit=False)
        staff.staffID = staff.staffID.upper().strip()
        staff.fullname = staff.fullname.upper().strip()
        staff.job_grade = staff.job_grade.upper().strip()
        staff.ghana_card = staff.ghana_card.upper().strip()
        staff.mgmt_units = staff.mgmt_units.upper().strip() if staff.mgmt_units else ''
        staff.bank = staff.bank.upper().strip() if staff.bank else ''
        staff.save()

        record_audit_log(
            user=self.request.user,
            action="PERSONNEL_UPDATED",
            target_type="NorminalRoll",
            target_id=staff.staffID,
            description=f"Updated details for {staff.fullname} ({staff.staffID}).",
            region=staff.current_region,
            district=staff.current_district,
            station=staff.station,
            post=staff.post,
            ip_address=self.request.META.get('REMOTE_ADDR')
        )

        messages.success(self.request, f"Personnel {staff.fullname} updated successfully.")
        return redirect('personnel')


class StaffView(LoginRequiredMixin, DetailView):
    model = NorminalRoll
    template_name = 'gnfs_komenda/staff-details.html'
    context_object_name = 'staff'
    slug_field = 'staffID'
    slug_url_kwarg = 'staffID'
    login_url = 'login-staff'

    def get_object(self, queryset=None):
        staffID = self.kwargs.get('staffID')
        staff = get_object_or_404(NorminalRoll, staffID=staffID)
        if not can_user_manage_personnel(self.request.user, staff):
            raise PermissionDenied("You are not authorized to view this personnel record.")
        return staff

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        staff = self.object
        today = timezone.localdate()
        context['leave_entitlements'] = staff.leave_entitlements.filter(leave_year=today.year).select_related('leave_type')
        context['leave_history'] = staff.leave_requests.all().select_related('leave_type', 'approved_by').order_by('-start_date')[:15]
        context['deferments'] = staff.deferment_requests.all().order_by('-created_at')[:10]
        return context


class DeleteStaff(LoginRequiredMixin, View):
    """
    Controlled Personnel Archival & Approval Request Handler.
    Replaces destructive deletion with compliant soft-archival / multi-tier approval workflow.
    """
    login_url = 'login-staff'

    def post(self, request, staffID, *args, **kwargs):
        staff = get_object_or_404(NorminalRoll, staffID=staffID)

        if not can_user_manage_personnel(request.user, staff):
            raise PermissionDenied("You are not authorized to remove or archive this personnel record.")

        # Self-deletion protection: An officer cannot delete/archive their own matching account
        if request.user.email and staff.ghana_card and request.user.email.lower() in staff.fullname.lower():
            messages.error(request, "Self-deletion prohibited: You cannot archive your own personnel record.")
            return redirect('personnel')

        role = get_user_role(request.user)
        reason = request.POST.get("reason", "").strip() or "Administrative archival requested via personnel roll."

        # National Admin / Superuser can soft-archive directly
        if request.user.is_superuser or role == "NATIONAL_ADMIN":
            with transaction.atomic():
                staff.is_active = False
                staff.archived_at = timezone.now()
                staff.archived_by = request.user
                staff.archive_reason = reason
                staff.save()

                record_audit_log(
                    user=request.user,
                    action="PERSONNEL_ARCHIVED_DIRECT",
                    target_type="NorminalRoll",
                    target_id=staff.staffID,
                    description=f"National Administrator {request.user.username} directly archived {staff.fullname} ({staff.staffID}). Reason: {reason}",
                    region=staff.current_region,
                    district=staff.current_district,
                    station=staff.station,
                    post=staff.post,
                    ip_address=request.META.get('REMOTE_ADDR')
                )

            messages.success(request, f"Personnel {staff.fullname} has been successfully archived.")
            return redirect('personnel')

        # Station, District, or Regional Admin initiates an approval request
        try:
            req = create_archive_request(
                personnel=staff,
                requester=request.user,
                reason=reason,
                ip_address=request.META.get('REMOTE_ADDR')
            )
            messages.info(
                request,
                f"Archive request submitted for {staff.fullname}. "
                f"In accordance with GNFS regulations, this request has been forwarded to the District Fire Officer (DFO) for review."
            )
        except ValidationError as e:
            messages.error(request, str(e.message if hasattr(e, 'message') else e))

        return redirect('personnel')


# ==============================================================================
# EXCEL IMPORT & EXPORT (SCOPED)
# ==============================================================================

class ExportAllPersonnelExcel(LoginRequiredMixin, View):
    login_url = 'login-staff'

    def get(self, request, *args, **kwargs):
        queryset = get_personnel_queryset_for_user(request.user).order_by('fullname')

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "GNFS Personnel"

        headers = [
            "Staff ID", "Full Name", "Job Grade", "Birth Date", "Ghana Card",
            "Region", "District", "Fire Station", "Market / Sub-Post",
            "Management Unit", "Bank", "Account Number", "Payroll Status",
            "Leave Status", "Leave Type", "Start Leave", "End Leave", "Active Status"
        ]
        ws.append(headers)

        for staff in queryset:
            row = [
                staff.staffID,
                staff.fullname,
                staff.job_grade,
                staff.birth_date.strftime("%Y-%m-%d") if staff.birth_date else "",
                staff.ghana_card,
                staff.station.district.region.name if staff.station and staff.station.district and staff.station.district.region else "",
                staff.station.district.name if staff.station and staff.station.district else "",
                staff.station.name if staff.station else "",
                staff.post.name if staff.post else "",
                staff.mgmt_units,
                staff.bank,
                staff.account_number,
                staff.payroll_status,
                staff.leave_status,
                staff.leave_type,
                staff.start_leave.strftime("%Y-%m-%d") if staff.start_leave else "",
                staff.end_leave.strftime("%Y-%m-%d") if staff.end_leave else "",
                "ACTIVE" if staff.is_active else "ARCHIVED"
            ]
            ws.append(row)

        current_date = now().strftime("%B_%Y")
        filename = f"GNFS_Personnel_Roll_{current_date}.xlsx"

        record_audit_log(
            user=request.user,
            action="PERSONNEL_EXPORT_EXCEL",
            target_type="NorminalRoll",
            target_id="ALL",
            description=f"Exported {queryset.count()} personnel records to Excel.",
            ip_address=request.META.get('REMOTE_ADDR')
        )

        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        wb.save(response)
        return response


class ImportPersonnelExcel(LoginRequiredMixin, View):
    template_name = "gnfs_komenda/import_personnel.html"
    login_url = 'login-staff'

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name)

    def post(self, request, *args, **kwargs):
        file = request.FILES.get("file")
        if not file:
            messages.error(request, "No file uploaded.")
            return redirect("import-personnel")

        user_asgn = get_user_assignment(request.user)
        default_station = user_asgn.station if user_asgn and user_asgn.station else None

        try:
            df = pd.read_excel(file)
            imported_count = 0
            skipped_rows = []

            for index, row in df.iterrows():
                try:
                    staffID = str(row.get("staffID")).strip()
                    if not staffID or staffID.lower() == "nan":
                        raise ValueError("Missing staffID")

                    NorminalRoll.objects.create(
                        staffID=staffID.upper(),
                        fullname=str(row.get("fullname")).strip().upper() if pd.notna(row.get("fullname")) else "",
                        job_grade=str(row.get("job_grade")).strip().upper() if pd.notna(row.get("job_grade")) else "",
                        birth_date=pd.to_datetime(row.get("birth_date"), errors="coerce").date() if pd.notna(row.get("birth_date")) else None,
                        ghana_card=str(row.get("ghana_card")).strip().upper() if pd.notna(row.get("ghana_card")) else None,
                        station=default_station,
                        mgmt_units=str(row.get("mgmt_units")).strip().upper() if pd.notna(row.get("mgmt_units")) else None,
                        bank=str(row.get("bank")).strip().upper() if pd.notna(row.get("bank")) else None,
                        account_number=str(row.get("account_number")).strip() if pd.notna(row.get("account_number")) else None,
                        payroll_status=str(row.get("payroll_status")).strip().upper() if pd.notna(row.get("payroll_status")) else "ACTIVE",
                        leave_type=str(row.get("leave_type")).strip().upper() if pd.notna(row.get("leave_type")) else "ANNUAL LEAVE",
                        leave_status=str(row.get("leave_status")).strip().upper() if pd.notna(row.get("leave_status")) else "AT POST",
                        start_leave=pd.to_datetime(row.get("start_leave"), errors="coerce").date() if pd.notna(row.get("start_leave")) else None,
                        end_leave=pd.to_datetime(row.get("end_leave"), errors="coerce").date() if pd.notna(row.get("end_leave")) else None,
                        called_off=bool(row.get("called_off")) if pd.notna(row.get("called_off")) else False,
                        is_active=True
                    )
                    imported_count += 1
                except Exception as e:
                    skipped_rows.append(f"Row {index + 1}: {e}")

            if skipped_rows:
                messages.warning(request, f"Imported {imported_count} records. Skipped {len(skipped_rows)} invalid rows.")
            else:
                messages.success(request, f"Successfully imported {imported_count} personnel records.")

        except Exception as e:
            messages.error(request, f"Error processing Excel import: {e}")

        return redirect("personnel")


# ==============================================================================
# HIERARCHICAL DASHBOARDS (NATIONAL -> REGIONAL -> DISTRICT -> STATION -> POST)
# ==============================================================================

class NationalDashboardView(LoginRequiredMixin, View):
    """National Headquarters Dashboard with nationwide visibility."""
    login_url = 'login-staff'

    def get(self, request, *args, **kwargs):
        role = get_user_role(request.user)
        if not (request.user.is_superuser or role == "NATIONAL_ADMIN"):
            messages.error(request, "Access restricted to National Administrators.")
            return redirect('personnel')

        NorminalRoll.sync_leaves()

        # Database Aggregate Metrics
        total_personnel = NorminalRoll.active_objects.count()
        senior_officers = NorminalRoll.active_objects.filter(officer_category=Rank.OfficerCategory.SENIOR).count()
        junior_officers = NorminalRoll.active_objects.filter(officer_category=Rank.OfficerCategory.JUNIOR).count()
        total_regions = Region.objects.filter(active=True).count()
        total_districts = District.objects.filter(active=True).count()
        total_stations = Station.objects.filter(active=True).count()
        total_posts = Post.objects.filter(active=True).count()
        personnel_at_post = NorminalRoll.active_objects.filter(leave_status="AT POST").count()
        personnel_on_leave = NorminalRoll.active_objects.filter(leave_status="ON LEAVE").count()
        pending_approvals = PersonnelActionRequest.objects.filter(status="PENDING_NATIONAL").count()

        # Query all regions with real-time aggregate counts
        regions = Region.objects.filter(active=True).annotate(
            district_count=Count('districts', distinct=True),
            station_count=Count('districts__stations', distinct=True),
            personnel_count=Count('districts__stations__personnel', filter=Q(districts__stations__personnel__is_active=True), distinct=True)
        ).order_by('name')

        return render(request, "gnfs_komenda/dashboard_national.html", {
            "total_personnel": total_personnel,
            "senior_officers": senior_officers,
            "junior_officers": junior_officers,
            "total_regions": total_regions,
            "total_districts": total_districts,
            "total_stations": total_stations,
            "total_posts": total_posts,
            "personnel_at_post": personnel_at_post,
            "personnel_on_leave": personnel_on_leave,
            "pending_approvals": pending_approvals,
            "regions": regions,
        })


class RegionalDashboardView(LoginRequiredMixin, View):
    """Regional Command Dashboard scoped to the specified region."""
    login_url = 'login-staff'

    def get(self, request, region_id, *args, **kwargs):
        region = get_object_or_404(Region, id=region_id, active=True)
        role = get_user_role(request.user)
        assignment = get_user_assignment(request.user)

        # Access Control: National Admin or Regional Admin for THIS region
        if not request.user.is_superuser and role != "NATIONAL_ADMIN":
            if role == "REGIONAL_ADMIN" and assignment and assignment.region_id != region.id:
                raise PermissionDenied("You are not authorized to access another Regional Command.")
            elif role not in ("REGIONAL_ADMIN", "NATIONAL_ADMIN"):
                raise PermissionDenied("Unauthorized regional dashboard access.")

        NorminalRoll.sync_leaves()

        scoped_personnel = NorminalRoll.active_objects.filter(station__district__region=region)
        total_personnel = scoped_personnel.count()
        senior_officers = scoped_personnel.filter(officer_category=Rank.OfficerCategory.SENIOR).count()
        junior_officers = scoped_personnel.filter(officer_category=Rank.OfficerCategory.JUNIOR).count()
        at_post = scoped_personnel.filter(leave_status="AT POST").count()
        on_leave = scoped_personnel.filter(leave_status="ON LEAVE").count()

        districts = District.objects.filter(region=region, active=True).annotate(
            station_count=Count('stations', distinct=True),
            post_count=Count('stations__posts', distinct=True),
            personnel_count=Count('stations__personnel', filter=Q(stations__personnel__is_active=True), distinct=True)
        ).order_by('name')

        pending_regional_approvals = PersonnelActionRequest.objects.filter(
            status="PENDING_REGIONAL",
            personnel__station__district__region=region
        ).count()

        return render(request, "gnfs_komenda/dashboard_regional.html", {
            "region": region,
            "total_personnel": total_personnel,
            "senior_officers": senior_officers,
            "junior_officers": junior_officers,
            "at_post": at_post,
            "on_leave": on_leave,
            "district_count": districts.count(),
            "station_count": Station.objects.filter(district__region=region, active=True).count(),
            "post_count": Post.objects.filter(station__district__region=region, active=True).count(),
            "pending_approvals": pending_regional_approvals,
            "districts": districts,
        })


class DistrictDashboardView(LoginRequiredMixin, View):
    """District Administration / DFO Dashboard scoped to the specified district."""
    login_url = 'login-staff'

    def get(self, request, district_id, *args, **kwargs):
        district = get_object_or_404(District, id=district_id, active=True)
        role = get_user_role(request.user)
        assignment = get_user_assignment(request.user)

        # Access check
        if not request.user.is_superuser and role != "NATIONAL_ADMIN":
            if role == "REGIONAL_ADMIN" and assignment and assignment.region_id != district.region_id:
                raise PermissionDenied("You are not authorized to access districts outside your region.")
            elif role in ("DFO", "DISTRICT_ADMIN") and assignment and assignment.district_id != district.id:
                raise PermissionDenied("You are not authorized to access another District.")

        NorminalRoll.sync_leaves()

        scoped_personnel = NorminalRoll.active_objects.filter(station__district=district)
        total_personnel = scoped_personnel.count()
        senior_officers = scoped_personnel.filter(officer_category=Rank.OfficerCategory.SENIOR).count()
        junior_officers = scoped_personnel.filter(officer_category=Rank.OfficerCategory.JUNIOR).count()
        at_post = scoped_personnel.filter(leave_status="AT POST").count()
        on_leave = scoped_personnel.filter(leave_status="ON LEAVE").count()

        stations = Station.objects.filter(district=district, active=True).annotate(
            post_count=Count('posts', distinct=True),
            personnel_count=Count('personnel', filter=Q(personnel__is_active=True), distinct=True)
        ).order_by('name')

        pending_dfo_approvals = PersonnelActionRequest.objects.filter(
            status="PENDING_DFO",
            personnel__station__district=district
        ).count()

        return render(request, "gnfs_komenda/dashboard_district.html", {
            "district": district,
            "region": district.region,
            "total_personnel": total_personnel,
            "senior_officers": senior_officers,
            "junior_officers": junior_officers,
            "at_post": at_post,
            "on_leave": on_leave,
            "station_count": stations.count(),
            "post_count": Post.objects.filter(station__district=district, active=True).count(),
            "pending_approvals": pending_dfo_approvals,
            "stations": stations,
        })


class StationDashboardView(LoginRequiredMixin, View):
    """Station Administrator Dashboard."""
    login_url = 'login-staff'

    def get(self, request, station_id, *args, **kwargs):
        station = get_object_or_404(Station, id=station_id, active=True)
        role = get_user_role(request.user)
        assignment = get_user_assignment(request.user)

        # Access check
        if not request.user.is_superuser and role != "NATIONAL_ADMIN":
            if role == "REGIONAL_ADMIN" and assignment and assignment.region_id != station.district.region_id:
                raise PermissionDenied("Access denied.")
            elif role in ("DFO", "DISTRICT_ADMIN") and assignment and assignment.district_id != station.district_id:
                raise PermissionDenied("Access denied.")
            elif role == "STATION_ADMIN" and assignment and assignment.station_id != station.id:
                raise PermissionDenied("You cannot access another Fire Station.")

        NorminalRoll.sync_leaves()

        personnel = NorminalRoll.active_objects.filter(station=station).order_by('fullname')
        total_personnel = personnel.count()
        senior_officers = personnel.filter(officer_category=Rank.OfficerCategory.SENIOR).count()
        junior_officers = personnel.filter(officer_category=Rank.OfficerCategory.JUNIOR).count()
        at_post = personnel.filter(leave_status="AT POST").count()
        on_leave = personnel.filter(leave_status="ON LEAVE").count()
        posts = Post.objects.filter(station=station, active=True)

        return render(request, "gnfs_komenda/dashboard_station.html", {
            "station": station,
            "district": station.district,
            "region": station.district.region,
            "total_personnel": total_personnel,
            "senior_officers": senior_officers,
            "junior_officers": junior_officers,
            "at_post": at_post,
            "on_leave": on_leave,
            "posts": posts,
            "personnel": personnel[:25],
        })


class PostDashboardView(LoginRequiredMixin, View):
    """Market Post / Sub-Post Dashboard."""
    login_url = 'login-staff'

    def get(self, request, post_id, *args, **kwargs):
        post = get_object_or_404(Post, id=post_id, active=True)
        role = get_user_role(request.user)
        assignment = get_user_assignment(request.user)

        # Access check
        if not request.user.is_superuser and role != "NATIONAL_ADMIN":
            if role == "POST_ADMIN" and assignment and assignment.post_id != post.id:
                raise PermissionDenied("You cannot access another Post.")

        NorminalRoll.sync_leaves()

        personnel = NorminalRoll.active_objects.filter(post=post).order_by('fullname')
        total_personnel = personnel.count()
        senior_officers = personnel.filter(officer_category=Rank.OfficerCategory.SENIOR).count()
        junior_officers = personnel.filter(officer_category=Rank.OfficerCategory.JUNIOR).count()
        at_post = personnel.filter(leave_status="AT POST").count()
        on_leave = personnel.filter(leave_status="ON LEAVE").count()

        return render(request, "gnfs_komenda/dashboard_post.html", {
            "post": post,
            "station": post.station,
            "district": post.station.district,
            "region": post.station.district.region,
            "total_personnel": total_personnel,
            "senior_officers": senior_officers,
            "junior_officers": junior_officers,
            "at_post": at_post,
            "on_leave": on_leave,
            "personnel": personnel,
        })


# ==============================================================================
# CASCADING DROPDOWN & LIVE SEARCH API ENDPOINTS (JSON)
# ==============================================================================

@login_required(login_url='login-staff')
def api_regions(request):
    """Return JSON list of Regions authorized for authenticated user."""
    qs = get_regions_queryset_for_user(request.user)
    data = [{"id": r.id, "name": r.name, "code": r.code} for r in qs]
    return JsonResponse({"regions": data})


@login_required(login_url='login-staff')
def api_districts(request):
    """Return JSON list of Districts optionally filtered by region_id."""
    region_id = request.GET.get("region_id")
    region = Region.objects.filter(id=region_id).first() if region_id else None
    qs = get_districts_queryset_for_user(request.user, region=region)
    data = [{"id": d.id, "name": d.name, "code": d.code} for d in qs]
    return JsonResponse({"districts": data})


@login_required(login_url='login-staff')
def api_stations(request):
    """Return JSON list of Stations optionally filtered by district_id."""
    district_id = request.GET.get("district_id")
    district = District.objects.filter(id=district_id).first() if district_id else None
    qs = get_stations_queryset_for_user(request.user, district=district)
    data = [{"id": s.id, "name": s.name, "code": s.code, "station_type": s.station_type} for s in qs]
    return JsonResponse({"stations": data})


@login_required(login_url='login-staff')
def api_posts(request):
    """Return JSON list of Posts optionally filtered by station_id."""
    station_id = request.GET.get("station_id")
    station = Station.objects.filter(id=station_id).first() if station_id else None
    qs = get_posts_queryset_for_user(request.user, station=station)
    data = [{"id": p.id, "name": p.name, "code": p.code, "post_type": p.post_type} for p in qs]
    return JsonResponse({"posts": data})


@login_required(login_url='login-staff')
def api_ranks(request):
    """Return JSON list of active GNFS Ranks, optionally filtered by officer_category."""
    category = request.GET.get("category", "").strip().upper()
    qs = Rank.objects.filter(active=True)
    if category in (Rank.OfficerCategory.SENIOR, Rank.OfficerCategory.JUNIOR):
        qs = qs.filter(officer_category=category)
    data = [
        {
            "id": r.id,
            "name": r.name,
            "abbreviation": r.abbreviation,
            "officer_category": r.officer_category,
            "officer_category_display": r.get_officer_category_display(),
            "hierarchy_order": r.hierarchy_order,
            "display": f"{r.name} ({r.abbreviation})" if r.abbreviation else r.name,
        }
        for r in qs.order_by("hierarchy_order")
    ]
    return JsonResponse({"ranks": data})


@login_required(login_url='login-staff')
def api_personnel_search(request):
    """
    Debounced live-search API returning strictly scoped JSON results.
    Prevents unauthorized information leakage across regional/district boundaries.
    """
    q = request.GET.get("q", "").strip()
    scoped_qs = get_personnel_queryset_for_user(request.user)

    if q:
        scoped_qs = scoped_qs.filter(
            Q(fullname__icontains=q) |
            Q(staffID__icontains=q) |
            Q(job_grade__icontains=q) |
            Q(rank__name__icontains=q) |
            Q(rank__abbreviation__icontains=q) |
            Q(ghana_card__icontains=q) |
            Q(station__name__icontains=q) |
            Q(station__district__name__icontains=q) |
            Q(station__district__region__name__icontains=q) |
            Q(post__name__icontains=q)
        )

    results = []
    for staff in scoped_qs[:30]:
        results.append({
            "staffID": staff.staffID,
            "fullname": staff.fullname,
            "job_grade": staff.job_grade,
            "rank": staff.rank_short,
            "rank_display": staff.rank_display,
            "officer_category": staff.officer_category,
            "officer_category_display": staff.get_officer_category_display() if staff.officer_category else "",
            "ghana_card": staff.ghana_card,
            "region": staff.station.district.region.name if staff.station and staff.station.district and staff.station.district.region else "N/A",
            "district": staff.station.district.name if staff.station and staff.station.district else "N/A",
            "station": staff.station.name if staff.station else "N/A",
            "post": staff.post.name if staff.post else "",
            "leave_status": staff.leave_status,
            "payroll_status": staff.payroll_status,
            "detail_url": reverse('staff-detail', kwargs={'staffID': staff.staffID}),
            "edit_url": reverse('editstaff', kwargs={'staffID': staff.staffID}),
            "delete_url": reverse('deletestaff', kwargs={'staffID': staff.staffID}),
        })

    return JsonResponse({
        "count": scoped_qs.count(),
        "query": q,
        "results": results
    })


# ==============================================================================
# APPROVAL WORKFLOW & ACTION REQUEST VIEWS
# ==============================================================================

@login_required(login_url='login-staff')
def approval_list_view(request):
    """View list of pending approval requests awaiting this user's decision."""
    role = get_user_role(request.user)
    assignment = get_user_assignment(request.user)

    pending_qs = PersonnelActionRequest.objects.none()
    if request.user.is_superuser or role == "NATIONAL_ADMIN":
        pending_qs = PersonnelActionRequest.objects.filter(status="PENDING_NATIONAL")
    elif role == "REGIONAL_ADMIN" and assignment and assignment.region:
        pending_qs = PersonnelActionRequest.objects.filter(
            status="PENDING_REGIONAL",
            personnel__station__district__region=assignment.region
        )
    elif role in ("DFO", "DISTRICT_ADMIN") and assignment and assignment.district:
        pending_qs = PersonnelActionRequest.objects.filter(
            status="PENDING_DFO",
            personnel__station__district=assignment.district
        )

    # User's own submitted requests
    my_requests = PersonnelActionRequest.objects.filter(requested_by=request.user)

    # Recent completed history for context
    history_qs = ApprovalHistory.objects.filter(approver=request.user).order_by('-timestamp')[:15]

    return render(request, "gnfs_komenda/approval_list.html", {
        "pending_requests": pending_qs,
        "my_requests": my_requests,
        "recent_history": history_qs,
        "role": role,
    })


@login_required(login_url='login-staff')
def approval_detail_view(request, request_id):
    """View details of a personnel action request and submit approval or rejection decision."""
    req_obj = get_object_or_404(PersonnelActionRequest, id=request_id)
    can_approve = can_user_approve_request(request.user, req_obj)

    if request.method == "POST":
        if not can_approve:
            raise PermissionDenied("You do not possess the required approval authority for this request.")

        decision = request.POST.get("decision", "").strip().upper()
        comment = request.POST.get("comment", "").strip()

        try:
            process_approval_decision(
                request_obj=req_obj,
                approver=request.user,
                decision=decision,
                comment=comment,
                ip_address=request.META.get('REMOTE_ADDR')
            )
            messages.success(request, f"Approval decision '{decision}' recorded successfully.")
            return redirect('approval-list')
        except ValidationError as e:
            messages.error(request, str(e.message if hasattr(e, 'message') else e))

    history = req_obj.history.all().order_by('timestamp')

    return render(request, "gnfs_komenda/approval_detail.html", {
        "req": req_obj,
        "personnel": req_obj.personnel,
        "history": history,
        "can_approve": can_approve,
    })


# ==============================================================================
# NOTIFICATIONS & AUDIT LOG VIEWS
# ==============================================================================

@login_required(login_url='login-staff')
def notifications_view(request):
    """In-app notifications center."""
    notes = Notification.objects.filter(recipient=request.user).order_by('-created_at')
    # Mark as read on viewing
    notes.filter(is_read=False).update(is_read=True)

    return render(request, "gnfs_komenda/notifications.html", {
        "notifications": notes
    })


@login_required(login_url='login-staff')
def audit_logs_view(request):
    """View tamper-evident audit logs within the user's jurisdiction."""
    role = get_user_role(request.user)
    assignment = get_user_assignment(request.user)

    if not (request.user.is_superuser or role in ("NATIONAL_ADMIN", "REGIONAL_ADMIN")):
        raise PermissionDenied("Audit logs are restricted to National and Regional Administrators.")

    qs = AuditLog.objects.all()
    if role == "REGIONAL_ADMIN" and assignment and assignment.region:
        qs = qs.filter(region=assignment.region)

    paginator = Paginator(qs, 30)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, "gnfs_komenda/audit_logs.html", {
        "page_obj": page_obj
    })


# ==============================================================================
# DYNAMIC STRUCTURE MANAGEMENT VIEWS (17 REGIONS, DISTRICTS, STATIONS, POSTS)
# ==============================================================================

@login_required(login_url='login-staff')
def structure_manage_view(request):
    """
    In-App Dynamic GNFS Organisational Structure Management Portal.
    Allows authorized administrators to browse, live-search, add, edit, and safely toggle/delete
    all 17 Service Administrative Regions, Districts, Fire Stations, and Posts.
    """
    user = request.user
    role = get_user_role(user)
    if not (user.is_superuser or user.is_staff or role in ('NATIONAL_ADMIN', 'REGIONAL_ADMIN')):
        messages.error(request, "Permission denied: Access to Organisational Structure Management is restricted to Administrators.")
        return redirect('personnel')

    active_tab = request.GET.get('tab', 'regions')
    q = request.GET.get('q', '').strip()

    # Load regions with aggregate counts
    regions = Region.objects.all().annotate(
        num_districts=Count('districts', distinct=True),
        num_stations=Count('districts__stations', distinct=True)
    ).order_by('name')

    # Load districts with aggregate counts
    districts = District.objects.all().select_related('region').annotate(
        num_stations=Count('stations', distinct=True),
        num_personnel=Count('stations__personnel', distinct=True)
    ).order_by('region__name', 'name')

    # Load stations with aggregate counts
    stations = Station.objects.all().select_related('district', 'district__region').annotate(
        num_posts=Count('posts', distinct=True),
        num_personnel=Count('personnel', distinct=True)
    ).order_by('district__region__name', 'district__name', 'name')

    # Load posts with aggregate counts
    posts = Post.objects.all().select_related('station', 'station__district', 'station__district__region').annotate(
        num_personnel=Count('personnel', distinct=True)
    ).order_by('station__name', 'name')

    # Scoped information for regional admins
    assignment = get_user_assignment(user)
    user_region_id = assignment.region.id if (assignment and assignment.region) else None

    # Filter by search query if provided via GET (client-side JS also does instantaneous filtering)
    if q:
        regions = regions.filter(Q(name__icontains=q) | Q(code__icontains=q))
        districts = districts.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(region__name__icontains=q))
        stations = stations.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(district__name__icontains=q) | Q(district__region__name__icontains=q))
        posts = posts.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(station__name__icontains=q))

    context = {
        'active_tab': active_tab,
        'search_query': q,
        'regions': regions,
        'districts': districts,
        'stations': stations,
        'posts': posts,
        'total_regions_count': Region.objects.count(),
        'total_districts_count': District.objects.count(),
        'total_stations_count': Station.objects.count(),
        'total_posts_count': Post.objects.count(),
        'active_regions_count': Region.objects.filter(active=True).count(),
        'active_districts_count': District.objects.filter(active=True).count(),
        'active_stations_count': Station.objects.filter(active=True).count(),
        'active_posts_count': Post.objects.filter(active=True).count(),
        'region_form': RegionForm(),
        'district_form': DistrictForm(),
        'station_form': StationForm(),
        'post_form': PostForm(),
        'user_role': role,
        'user_region_id': user_region_id,
        'can_edit_all': (user.is_superuser or user.is_staff or role == 'NATIONAL_ADMIN'),
    }
    return render(request, "gnfs_komenda/structure_manage.html", context)


@login_required(login_url='login-staff')
@require_POST
def add_region(request):
    if not (request.user.is_superuser or request.user.is_staff or get_user_role(request.user) == 'NATIONAL_ADMIN'):
        messages.error(request, "Permission denied: Only National Administrators can add regions.")
        return redirect('structure-manage')
    form = RegionForm(request.POST)
    if form.is_valid():
        reg = form.save()
        record_audit_log(
            user=request.user,
            action="STRUCTURE_ADD_REGION",
            target_type="Region",
            target_id=str(reg.id),
            description=f"Created Region '{reg.name}' ({reg.code})"
        )
        messages.success(request, f"Region '{reg.name}' ({reg.code}) successfully created.")
    else:
        err_msg = "; ".join([f"{f}: {', '.join(e)}" for f, e in form.errors.items()])
        messages.error(request, f"Could not create Region: {err_msg}")
    return redirect(f"{reverse('structure-manage')}?tab=regions")


@login_required(login_url='login-staff')
@require_POST
def edit_region(request, pk):
    if not (request.user.is_superuser or request.user.is_staff or get_user_role(request.user) == 'NATIONAL_ADMIN'):
        messages.error(request, "Permission denied: Only National Administrators can edit regions.")
        return redirect('structure-manage')
    region = get_object_or_404(Region, pk=pk)
    form = RegionForm(request.POST, instance=region)
    if form.is_valid():
        reg = form.save()
        record_audit_log(
            user=request.user,
            action="STRUCTURE_EDIT_REGION",
            target_type="Region",
            target_id=str(reg.id),
            description=f"Updated Region '{reg.name}' ({reg.code})"
        )
        messages.success(request, f"Region '{reg.name}' successfully updated.")
    else:
        err_msg = "; ".join([f"{f}: {', '.join(e)}" for f, e in form.errors.items()])
        messages.error(request, f"Could not update Region: {err_msg}")
    return redirect(f"{reverse('structure-manage')}?tab=regions")


@login_required(login_url='login-staff')
@require_POST
def add_district(request):
    if not (request.user.is_superuser or request.user.is_staff or get_user_role(request.user) in ('NATIONAL_ADMIN', 'REGIONAL_ADMIN')):
        messages.error(request, "Permission denied: Only Administrators can add districts.")
        return redirect('structure-manage')
    form = DistrictForm(request.POST)
    if form.is_valid():
        dist = form.save()
        record_audit_log(
            user=request.user,
            action="STRUCTURE_ADD_DISTRICT",
            target_type="District",
            target_id=str(dist.id),
            description=f"Created District '{dist.name}' in {dist.region.name}",
            region=dist.region
        )
        messages.success(request, f"District '{dist.name}' successfully created in {dist.region.name}.")
    else:
        err_msg = "; ".join([f"{f}: {', '.join(e)}" for f, e in form.errors.items()])
        messages.error(request, f"Could not create District: {err_msg}")
    return redirect(f"{reverse('structure-manage')}?tab=districts")


@login_required(login_url='login-staff')
@require_POST
def edit_district(request, pk):
    if not (request.user.is_superuser or request.user.is_staff or get_user_role(request.user) in ('NATIONAL_ADMIN', 'REGIONAL_ADMIN')):
        messages.error(request, "Permission denied: Only Administrators can edit districts.")
        return redirect('structure-manage')
    district = get_object_or_404(District, pk=pk)
    form = DistrictForm(request.POST, instance=district)
    if form.is_valid():
        dist = form.save()
        record_audit_log(
            user=request.user,
            action="STRUCTURE_EDIT_DISTRICT",
            target_type="District",
            target_id=str(dist.id),
            description=f"Updated District '{dist.name}'",
            region=dist.region
        )
        messages.success(request, f"District '{dist.name}' successfully updated.")
    else:
        err_msg = "; ".join([f"{f}: {', '.join(e)}" for f, e in form.errors.items()])
        messages.error(request, f"Could not update District: {err_msg}")
    return redirect(f"{reverse('structure-manage')}?tab=districts")


@login_required(login_url='login-staff')
@require_POST
def add_station(request):
    if not (request.user.is_superuser or request.user.is_staff or get_user_role(request.user) in ('NATIONAL_ADMIN', 'REGIONAL_ADMIN')):
        messages.error(request, "Permission denied: Only Administrators can add stations.")
        return redirect('structure-manage')
    form = StationForm(request.POST)
    if form.is_valid():
        station = form.save()
        record_audit_log(
            user=request.user,
            action="STRUCTURE_ADD_STATION",
            target_type="Station",
            target_id=str(station.id),
            description=f"Created Station '{station.name}' under {station.district.name}",
            region=station.district.region,
            station=station
        )
        messages.success(request, f"Station '{station.name}' successfully created.")
    else:
        err_msg = "; ".join([f"{f}: {', '.join(e)}" for f, e in form.errors.items()])
        messages.error(request, f"Could not create Station: {err_msg}")
    return redirect(f"{reverse('structure-manage')}?tab=stations")


@login_required(login_url='login-staff')
@require_POST
def edit_station(request, pk):
    if not (request.user.is_superuser or request.user.is_staff or get_user_role(request.user) in ('NATIONAL_ADMIN', 'REGIONAL_ADMIN')):
        messages.error(request, "Permission denied: Only Administrators can edit stations.")
        return redirect('structure-manage')
    station = get_object_or_404(Station, pk=pk)
    form = StationForm(request.POST, instance=station)
    if form.is_valid():
        stn = form.save()
        record_audit_log(
            user=request.user,
            action="STRUCTURE_EDIT_STATION",
            target_type="Station",
            target_id=str(stn.id),
            description=f"Updated Station '{stn.name}'",
            region=stn.district.region,
            station=stn
        )
        messages.success(request, f"Station '{stn.name}' successfully updated.")
    else:
        err_msg = "; ".join([f"{f}: {', '.join(e)}" for f, e in form.errors.items()])
        messages.error(request, f"Could not update Station: {err_msg}")
    return redirect(f"{reverse('structure-manage')}?tab=stations")


@login_required(login_url='login-staff')
@require_POST
def add_post(request):
    if not (request.user.is_superuser or request.user.is_staff or get_user_role(request.user) in ('NATIONAL_ADMIN', 'REGIONAL_ADMIN')):
        messages.error(request, "Permission denied: Only Administrators can add posts.")
        return redirect('structure-manage')
    form = PostForm(request.POST)
    if form.is_valid():
        p = form.save()
        record_audit_log(
            user=request.user,
            action="STRUCTURE_ADD_POST",
            target_type="Post",
            target_id=str(p.id),
            description=f"Created Post '{p.name}' under {p.station.name}",
            region=p.station.district.region,
            station=p.station,
            post=p
        )
        messages.success(request, f"Post '{p.name}' successfully created.")
    else:
        err_msg = "; ".join([f"{f}: {', '.join(e)}" for f, e in form.errors.items()])
        messages.error(request, f"Could not create Post: {err_msg}")
    return redirect(f"{reverse('structure-manage')}?tab=posts")


@login_required(login_url='login-staff')
@require_POST
def edit_post(request, pk):
    if not (request.user.is_superuser or request.user.is_staff or get_user_role(request.user) in ('NATIONAL_ADMIN', 'REGIONAL_ADMIN')):
        messages.error(request, "Permission denied: Only Administrators can edit posts.")
        return redirect('structure-manage')
    p = get_object_or_404(Post, pk=pk)
    form = PostForm(request.POST, instance=p)
    if form.is_valid():
        post_obj = form.save()
        record_audit_log(
            user=request.user,
            action="STRUCTURE_EDIT_POST",
            target_type="Post",
            target_id=str(post_obj.id),
            description=f"Updated Post '{post_obj.name}'",
            region=post_obj.station.district.region,
            station=post_obj.station,
            post=post_obj
        )
        messages.success(request, f"Post '{post_obj.name}' successfully updated.")
    else:
        err_msg = "; ".join([f"{f}: {', '.join(e)}" for f, e in form.errors.items()])
        messages.error(request, f"Could not update Post: {err_msg}")
    return redirect(f"{reverse('structure-manage')}?tab=posts")


@login_required(login_url='login-staff')
@require_POST
def toggle_unit_active(request, unit_type, pk):
    if not (request.user.is_superuser or request.user.is_staff or get_user_role(request.user) in ('NATIONAL_ADMIN', 'REGIONAL_ADMIN')):
        return JsonResponse({"status": "error", "message": "Permission denied."}, status=403)

    model_map = {'region': Region, 'district': District, 'station': Station, 'post': Post}
    tab_map = {'region': 'regions', 'district': 'districts', 'station': 'stations', 'post': 'posts'}
    model = model_map.get(unit_type)
    if not model:
        return JsonResponse({"status": "error", "message": "Invalid unit type."}, status=400)

    obj = get_object_or_404(model, pk=pk)
    obj.active = not obj.active
    obj.save(update_fields=['active', 'updated_at'])

    status_str = "Active" if obj.active else "Inactive"
    record_audit_log(
        user=request.user,
        action="STRUCTURE_TOGGLE_ACTIVE",
        target_type=model.__name__,
        target_id=str(obj.id),
        description=f"Toggled {model.__name__} '{obj.name}' status to {status_str}"
    )
    messages.success(request, f"{model.__name__} '{obj.name}' is now marked as {status_str}.")

    tab = tab_map.get(unit_type, 'regions')
    return redirect(f"{reverse('structure-manage')}?tab={tab}")


@login_required(login_url='login-staff')
@require_POST
def delete_unit(request, unit_type, pk):
    if not (request.user.is_superuser or request.user.is_staff or get_user_role(request.user) == 'NATIONAL_ADMIN'):
        messages.error(request, "Permission denied: Only National Administrators can delete organizational units.")
        return redirect('structure-manage')

    model_map = {'region': Region, 'district': District, 'station': Station, 'post': Post}
    tab_map = {'region': 'regions', 'district': 'districts', 'station': 'stations', 'post': 'posts'}
    model = model_map.get(unit_type)
    if not model:
        messages.error(request, "Invalid unit type.")
        return redirect('structure-manage')

    obj = get_object_or_404(model, pk=pk)
    name = obj.name
    tab = tab_map.get(unit_type, 'regions')

    # Safe dependency validation to prevent orphaned records or ProtectedErrors
    has_children = False
    child_desc = ""

    if unit_type == 'region':
        c = obj.districts.count()
        if c > 0:
            has_children = True
            child_desc = f"{c} district(s)"
    elif unit_type == 'district':
        c = obj.stations.count()
        if c > 0:
            has_children = True
            child_desc = f"{c} station(s)"
    elif unit_type == 'station':
        p_count = obj.personnel.count()
        post_count = obj.posts.count()
        if p_count > 0 or post_count > 0:
            has_children = True
            child_desc = f"{p_count} personnel and {post_count} post(s)"
    elif unit_type == 'post':
        p_count = obj.personnel.count()
        if p_count > 0:
            has_children = True
            child_desc = f"{p_count} personnel"

    if has_children:
        messages.warning(
            request,
            f"Cannot delete {model.__name__} '{name}': It has {child_desc} attached. "
            f"Please toggle this unit to 'Inactive' instead, or reassign all attached records first."
        )
        return redirect(f"{reverse('structure-manage')}?tab={tab}")

    try:
        obj.delete()
        record_audit_log(
            user=request.user,
            action="STRUCTURE_DELETE_UNIT",
            target_type=model.__name__,
            target_id=str(pk),
            description=f"Deleted {model.__name__} '{name}'"
        )
        messages.success(request, f"{model.__name__} '{name}' successfully deleted.")
    except Exception as e:
        messages.error(request, f"Unable to delete {model.__name__} '{name}': {str(e)}")

    return redirect(f"{reverse('structure-manage')}?tab={tab}")


@login_required(login_url='login-staff')
def api_structure_search(request):
    """
    Unified Live Search endpoint across all 4 tiers of the GNFS hierarchy.
    Returns matched counts and lightweight results for instantaneous UI filtering.
    """
    q = request.GET.get('q', '').strip()
    if not q:
        return JsonResponse({"query": "", "regions": [], "districts": [], "stations": [], "posts": []})

    regions = list(Region.objects.filter(
        Q(name__icontains=q) | Q(code__icontains=q)
    ).values('id', 'name', 'code', 'active')[:25])

    districts = list(District.objects.filter(
        Q(name__icontains=q) | Q(code__icontains=q) | Q(region__name__icontains=q)
    ).values('id', 'name', 'code', 'region__name', 'active')[:25])

    stations = list(Station.objects.filter(
        Q(name__icontains=q) | Q(code__icontains=q) | Q(district__name__icontains=q) | Q(district__region__name__icontains=q)
    ).values('id', 'name', 'code', 'station_type', 'district__name', 'district__region__name', 'active')[:25])

    posts = list(Post.objects.filter(
        Q(name__icontains=q) | Q(code__icontains=q) | Q(station__name__icontains=q)
    ).values('id', 'name', 'code', 'post_type', 'station__name', 'active')[:25])

    return JsonResponse({
        "query": q,
        "regions": regions,
        "districts": districts,
        "stations": stations,
        "posts": posts,
        "counts": {
            "regions": len(regions),
            "districts": len(districts),
            "stations": len(stations),
            "posts": len(posts)
        }
    })


# ==============================================================================
# ENTERPRISE LEAVE MANAGEMENT VIEWS
# ==============================================================================

import calendar
from datetime import datetime, date
from gnfs_komenda.models import (
    LeaveType, PublicHoliday, LeaveEntitlement, LeaveRequest,
    LeaveApprovalDecision, LeaveDefermentRequest, LeaveAdjustmentAudit
)
from gnfs_komenda.forms import (
    LeaveRequestForm, LeaveDefermentForm, LeaveRecallForm,
    LeaveAdjustmentForm, PublicHolidayForm
)
from gnfs_komenda.services.leave_calculator import calculate_leave_dates
from gnfs_komenda.services.leave_approval_service import (
    submit_leave_request, approve_leave_request, reject_leave_request, can_user_approve_leave
)
from gnfs_komenda.services.leave_deferment_service import (
    request_leave_deferment, approve_leave_deferment, reject_leave_deferment, can_user_approve_deferment
)
from gnfs_komenda.services.leave_recall_service import recall_officer_from_leave


@login_required(login_url='login-staff')
def api_leave_calculate(request):
    """Live interactive calculation API for dynamic leave application preview."""
    try:
        start_date_str = request.GET.get('start_date') or request.POST.get('start_date')
        duration_str = request.GET.get('duration') or request.POST.get('duration')
        leave_type_id = request.GET.get('leave_type_id') or request.POST.get('leave_type_id')
        calc_mode = request.GET.get('calculation_mode') or request.POST.get('calculation_mode')
        end_date_str = request.GET.get('end_date') or request.POST.get('end_date')

        if not start_date_str:
            return JsonResponse({'success': False, 'error': 'start_date is required'}, status=400)

        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        duration = int(duration_str) if duration_str else 1
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date() if end_date_str else None

        if leave_type_id and not calc_mode:
            leave_type = get_object_or_404(LeaveType, pk=leave_type_id)
            calc_mode = leave_type.calculation_mode

        if not calc_mode:
            calc_mode = LeaveType.CalculationMode.WORKING_DAYS

        result = calculate_leave_dates(
            start_date=start_date,
            duration=duration,
            calculation_mode=calc_mode,
            end_date=end_date
        )

        return JsonResponse({
            'success': True,
            'start_date': result['start_date'].isoformat(),
            'last_leave_date': result['last_leave_date'].isoformat(),
            'resume_date': result['resume_date'].isoformat(),
            'duration': result['duration'],
            'working_days_count': result['working_days_count'],
            'calendar_days_count': result['calendar_days_count'],
            'holidays_encountered': result['holidays_encountered'],
            'weekend_days_count': result['weekend_days_count'],
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required(login_url='login-staff')
def api_leave_entitlement(request):
    """API returning live entitlement balance for given officer, leave type, and year."""
    try:
        personnel_id = request.GET.get('personnel_id')
        staffID = request.GET.get('staffID')
        leave_type_id = request.GET.get('leave_type_id')
        year = int(request.GET.get('year', timezone.localdate().year))

        if personnel_id:
            personnel = get_object_or_404(NorminalRoll, pk=personnel_id)
        elif staffID:
            personnel = get_object_or_404(NorminalRoll, staffID=staffID)
        else:
            return JsonResponse({'success': False, 'error': 'personnel_id or staffID is required'}, status=400)

        if not can_user_manage_personnel(request.user, personnel):
            return JsonResponse({'success': False, 'error': 'Permission denied'}, status=403)

        if leave_type_id:
            leave_type = get_object_or_404(LeaveType, pk=leave_type_id)
        else:
            leave_type = LeaveType.objects.filter(code="ANNUAL").first()

        entitlement, created = LeaveEntitlement.objects.get_or_create(
            personnel=personnel,
            leave_year=year,
            leave_type=leave_type,
            defaults={
                'base_entitlement': leave_type.get_entitlement_for_category(personnel.officer_category)
            }
        )

        return JsonResponse({
            'success': True,
            'personnel_id': personnel.id,
            'staffID': personnel.staffID,
            'fullname': personnel.fullname,
            'officer_category': personnel.officer_category,
            'gender': getattr(personnel, 'gender', 'MALE'),
            'rank': personnel.rank_short,
            'leave_year': year,
            'leave_type': leave_type.name,
            'leave_type_code': leave_type.code,
            'allowed_gender': getattr(leave_type, 'allowed_gender', 'ALL'),
            'calculation_mode': leave_type.calculation_mode,
            'default_days': leave_type.get_entitlement_for_category(personnel.officer_category),
            'base_entitlement': entitlement.base_entitlement,
            'carried_forward': entitlement.carried_forward,
            'adjustments': entitlement.adjustments,
            'total_available': entitlement.total_available,
            'days_taken': entitlement.days_taken,
            'days_reserved': entitlement.days_reserved,
            'remaining_balance': entitlement.remaining_balance
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required(login_url='login-staff')
def leave_dashboard_view(request):
    """Central Leave Roster and Operations Dashboard."""
    scoped_personnel = get_personnel_queryset_for_user(request.user)
    leave_requests_qs = LeaveRequest.objects.filter(
        personnel__in=scoped_personnel
    ).select_related('personnel', 'leave_type', 'requested_by', 'approved_by', 'personnel__station')

    status_filter = request.GET.get('status', 'all')
    if status_filter != 'all' and status_filter in LeaveRequest.Status.values:
        leave_requests_qs = leave_requests_qs.filter(status=status_filter)

    search_query = request.GET.get('q', '').strip()
    if search_query:
        leave_requests_qs = leave_requests_qs.filter(
            Q(personnel__staffID__icontains=search_query) |
            Q(personnel__fullname__icontains=search_query) |
            Q(leave_type__name__icontains=search_query)
        )

    # Scoped metrics
    total_personnel = scoped_personnel.count()
    on_leave_count = scoped_personnel.filter(leave_status="ON LEAVE").count()
    at_post_count = scoped_personnel.filter(leave_status="AT POST").count()
    pending_approvals_count = LeaveRequest.objects.filter(
        personnel__in=scoped_personnel,
        status=LeaveRequest.Status.PENDING_APPROVAL
    ).count()
    pending_deferments_count = LeaveDefermentRequest.objects.filter(
        personnel__in=scoped_personnel,
        status=LeaveDefermentRequest.Status.PENDING
    ).count()

    paginator = Paginator(leave_requests_qs.order_by('-start_date', '-created_at'), 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'gnfs_komenda/leave/dashboard.html', {
        'page_obj': page_obj,
        'status_filter': status_filter,
        'search_query': search_query,
        'total_personnel': total_personnel,
        'on_leave_count': on_leave_count,
        'at_post_count': at_post_count,
        'pending_approvals_count': pending_approvals_count,
        'pending_deferments_count': pending_deferments_count,
    })


@login_required(login_url='login-staff')
def leave_apply_view(request):
    """Dynamic Leave Application View with Live Calculation."""
    if request.method == 'POST':
        form = LeaveRequestForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            personnel = form.cleaned_data['personnel']
            leave_type = form.cleaned_data['leave_type']
            start_date = form.cleaned_data['start_date']
            duration = form.cleaned_data['duration']
            reason = form.cleaned_data['reason']
            contact_phone = form.cleaned_data.get('contact_phone', '')
            contact_address = form.cleaned_data.get('contact_address', '')
            handover_notes = form.cleaned_data.get('handover_notes', '')
            med_ref = form.cleaned_data.get('medical_reference', '')

            is_proportionate = form.cleaned_data.get('is_proportionate', False)
            attachment = request.FILES.get('attachment')

            try:
                leave_req = submit_leave_request(
                    personnel=personnel,
                    leave_type=leave_type,
                    start_date=start_date,
                    duration=duration,
                    requested_by=request.user,
                    reason=reason,
                    contact_phone=contact_phone,
                    contact_address=contact_address,
                    handover_notes=handover_notes,
                    medical_reference=med_ref,
                    attachment=attachment,
                    is_proportionate=is_proportionate
                )

                messages.success(request, f"Leave application for {personnel.fullname} submitted successfully.")
                return redirect('leave-detail', pk=leave_req.pk)
            except (ValidationError, Exception) as e:
                messages.error(request, str(e))
    else:
        prefill_staff = request.GET.get('staffID')
        initial_data = {}
        if prefill_staff:
            p = NorminalRoll.objects.filter(staffID=prefill_staff).first()
            if p:
                initial_data['personnel'] = p.id
        form = LeaveRequestForm(initial=initial_data, user=request.user)

    leave_types = LeaveType.objects.filter(active=True).order_by('name')
    return render(request, 'gnfs_komenda/leave/apply.html', {
        'form': form,
        'leave_types': leave_types,
    })


@login_required(login_url='login-staff')
def leave_detail_view(request, pk):
    """Leave Request Detail View showing calculations, timeline, and actions."""
    leave_req = get_object_or_404(
        LeaveRequest.objects.select_related(
            'personnel', 'leave_type', 'entitlement', 'requested_by', 'approved_by', 'recalled_by',
            'personnel__station', 'personnel__station__district', 'personnel__station__district__region'
        ),
        pk=pk
    )

    if not can_user_manage_personnel(request.user, leave_req.personnel):
        raise PermissionDenied("You do not have permission to view this leave record.")

    can_approve = can_user_approve_leave(request.user, leave_req)
    decisions = leave_req.decisions.all().select_related('approver').order_by('timestamp')

    return render(request, 'gnfs_komenda/leave/detail.html', {
        'leave_req': leave_req,
        'can_approve': can_approve,
        'decisions': decisions,
    })


@login_required(login_url='login-staff')
def leave_approve_view(request, pk):
    """POST-only view to approve leave request."""
    if request.method != 'POST':
        return redirect('leave-detail', pk=pk)

    comments = request.POST.get('comments', '').strip()
    try:
        approve_leave_request(pk, request.user, comments=comments)
        messages.success(request, f"Leave request #{pk} has been successfully approved.")
    except PermissionDenied as e:
        messages.error(request, str(e))
    except ValidationError as e:
        messages.error(request, str(e))
    except Exception as e:
        messages.error(request, f"Error processing approval: {str(e)}")

    return redirect('leave-detail', pk=pk)


@login_required(login_url='login-staff')
def leave_reject_view(request, pk):
    """POST-only view to reject leave request."""
    if request.method != 'POST':
        return redirect('leave-detail', pk=pk)

    comments = request.POST.get('comments', '').strip()
    if not comments:
        messages.error(request, "A reason/comment is required when rejecting a leave request.")
        return redirect('leave-detail', pk=pk)

    try:
        reject_leave_request(pk, request.user, comments=comments)
        messages.warning(request, f"Leave request #{pk} has been rejected.")
    except PermissionDenied as e:
        messages.error(request, str(e))
    except ValidationError as e:
        messages.error(request, str(e))
    except Exception as e:
        messages.error(request, f"Error processing rejection: {str(e)}")

    return redirect('leave-detail', pk=pk)


@login_required(login_url='login-staff')
def leave_recall_view(request, pk):
    """Recall an officer from approved or active leave."""
    leave_req = get_object_or_404(LeaveRequest, pk=pk)
    if not can_user_approve_leave(request.user, leave_req):
        raise PermissionDenied("You do not have jurisdiction to recall this officer.")

    if request.method == 'POST':
        form = LeaveRecallForm(request.POST)
        if form.is_valid():
            recall_date = form.cleaned_data['recall_date']
            recall_reason = form.cleaned_data['recall_reason']
            try:
                recall_officer_from_leave(pk, request.user, recall_date, recall_reason)
                messages.success(request, f"Officer {leave_req.personnel.staffID} successfully recalled from leave.")
                return redirect('leave-detail', pk=pk)
            except Exception as e:
                messages.error(request, str(e))
    else:
        form = LeaveRecallForm(initial={'recall_date': timezone.localdate()})

    return render(request, 'gnfs_komenda/leave/recall.html', {
        'leave_req': leave_req,
        'form': form,
    })


@login_required(login_url='login-staff')
def leave_balances_view(request):
    """View personnel leave balances for the selected year."""
    scoped_personnel = get_personnel_queryset_for_user(request.user)
    year = int(request.GET.get('year', timezone.localdate().year))

    annual_type = LeaveType.objects.filter(code="ANNUAL").first()
    casual_type = LeaveType.objects.filter(code="CASUAL").first()

    search_query = request.GET.get('q', '').strip()
    if search_query:
        scoped_personnel = scoped_personnel.filter(
            Q(staffID__icontains=search_query) | Q(fullname__icontains=search_query)
        )

    # Prefetch entitlements
    entitlements = LeaveEntitlement.objects.filter(
        personnel__in=scoped_personnel,
        leave_year=year
    ).select_related('personnel', 'leave_type')

    # Build mapping for table
    personnel_balances = []
    ent_map = {}
    for ent in entitlements:
        if ent.personnel_id not in ent_map:
            ent_map[ent.personnel_id] = {}
        ent_map[ent.personnel_id][ent.leave_type.code] = ent

    for p in scoped_personnel.order_by('rank__hierarchy_order', 'fullname'):
        p_ents = ent_map.get(p.id, {})
        annual_ent = p_ents.get('ANNUAL')
        casual_ent = p_ents.get('CASUAL')
        personnel_balances.append({
            'personnel': p,
            'annual': annual_ent,
            'casual': casual_ent,
        })

    paginator = Paginator(personnel_balances, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'gnfs_komenda/leave/balances.html', {
        'page_obj': page_obj,
        'year': year,
        'years_list': [year - 1, year, year + 1],
        'search_query': search_query,
    })


@login_required(login_url='login-staff')
def leave_adjust_balance_view(request, entitlement_id):
    """POST-only view to adjust an entitlement balance with audit logging."""
    if request.method != 'POST':
        return redirect('leave-balances')

    entitlement = get_object_or_404(
        LeaveEntitlement.objects.select_related('personnel', 'leave_type'),
        pk=entitlement_id
    )

    if not (request.user.is_superuser or get_user_role(request.user) == "NATIONAL_ADMIN"):
        raise PermissionDenied("Only National Administrators can manually adjust leave entitlements.")

    form = LeaveAdjustmentForm(request.POST)
    if form.is_valid():
        days_adjusted = form.cleaned_data['days_adjusted']
        reason = form.cleaned_data['reason']

        with transaction.atomic():
            prev_balance = entitlement.remaining_balance
            entitlement.adjustments += days_adjusted
            entitlement.save(update_fields=['adjustments'])
            new_balance = entitlement.remaining_balance

            LeaveAdjustmentAudit.objects.create(
                entitlement=entitlement,
                adjusted_by=request.user,
                days_adjusted=days_adjusted,
                previous_balance=prev_balance,
                new_balance=new_balance,
                reason=reason
            )

        messages.success(
            request,
            f"Successfully adjusted {entitlement.personnel.staffID} {entitlement.leave_type.name} balance by {days_adjusted:+d} days."
        )
    else:
        messages.error(request, "Invalid adjustment data.")

    return redirect('leave-balances')


@login_required(login_url='login-staff')
def leave_calendar_view(request):
    """Interactive Leave Calendar & Roster."""
    scoped_personnel = get_personnel_queryset_for_user(request.user)
    today = timezone.localdate()

    month = int(request.GET.get('month', today.month))
    year = int(request.GET.get('year', today.year))

    # Active and upcoming leaves covering this month
    start_of_month = date(year, month, 1)
    end_of_month = date(year, month, calendar.monthrange(year, month)[1])

    leaves = LeaveRequest.objects.filter(
        personnel__in=scoped_personnel,
        status__in=[LeaveRequest.Status.APPROVED, LeaveRequest.Status.ACTIVE, LeaveRequest.Status.COMPLETED],
        start_date__lte=end_of_month,
        last_leave_date__gte=start_of_month
    ).select_related('personnel', 'leave_type', 'personnel__station').order_by('start_date')

    holidays = PublicHoliday.objects.filter(
        active=True,
        date__gte=start_of_month,
        date__lte=end_of_month
    ).order_by('date')

    return render(request, 'gnfs_komenda/leave/calendar.html', {
        'leaves': leaves,
        'holidays': holidays,
        'current_month': month,
        'current_year': year,
        'month_name': calendar.month_name[month],
    })


@login_required(login_url='login-staff')
def leave_deferments_list_view(request):
    """List Deferment Requests within user jurisdiction."""
    scoped_personnel = get_personnel_queryset_for_user(request.user)
    deferments = LeaveDefermentRequest.objects.filter(
        personnel__in=scoped_personnel
    ).select_related('personnel', 'approved_by', 'personnel__station').order_by('-created_at')

    return render(request, 'gnfs_komenda/leave/deferments.html', {
        'deferments': deferments,
    })


@login_required(login_url='login-staff')
def leave_deferment_apply_view(request):
    """Submit a Leave Deferment Request."""
    if request.method == 'POST':
        form = LeaveDefermentForm(request.POST, user=request.user)
        if form.is_valid():
            personnel = form.cleaned_data['personnel']
            leave_year = form.cleaned_data['leave_year']
            days_requested = form.cleaned_data['days_requested']
            reason = form.cleaned_data['reason']

            try:
                deferment = request_leave_deferment(
                    personnel=personnel,
                    leave_year=leave_year,
                    days_requested=days_requested,
                    reason=reason,
                    user=request.user
                )
                messages.success(request, f"Deferment request for {personnel.fullname} submitted successfully.")
                return redirect('leave-deferments')
            except Exception as e:
                messages.error(request, str(e))
    else:
        form = LeaveDefermentForm(initial={'leave_year': timezone.localdate().year}, user=request.user)

    return render(request, 'gnfs_komenda/leave/deferment_apply.html', {
        'form': form,
    })


@login_required(login_url='login-staff')
def leave_deferment_approve_view(request, pk):
    """POST view to approve deferment request."""
    if request.method != 'POST':
        return redirect('leave-deferments')

    deferment = get_object_or_404(LeaveDefermentRequest, pk=pk)
    try:
        approved_days = int(request.POST.get('approved_days', deferment.days_requested))
        remarks = request.POST.get('remarks', '').strip()
        approve_leave_deferment(pk, request.user, approved_days=approved_days, remarks=remarks)
        messages.success(request, f"Deferment #{pk} approved. {approved_days} days carried forward.")
    except Exception as e:
        messages.error(request, str(e))

    return redirect('leave-deferments')


@login_required(login_url='login-staff')
def leave_deferment_reject_view(request, pk):
    """POST view to reject deferment request."""
    if request.method != 'POST':
        return redirect('leave-deferments')

    remarks = request.POST.get('remarks', '').strip()
    try:
        reject_leave_deferment(pk, request.user, remarks=remarks)
        messages.warning(request, f"Deferment #{pk} has been rejected.")
    except Exception as e:
        messages.error(request, str(e))

    return redirect('leave-deferments')


@login_required(login_url='login-staff')
def public_holidays_view(request):
    """Manage dynamic Public Holidays."""
    year = int(request.GET.get('year', timezone.localdate().year))
    holidays = PublicHoliday.objects.filter(year=year).order_by('date')
    form = PublicHolidayForm()

    return render(request, 'gnfs_komenda/leave/holidays.html', {
        'holidays': holidays,
        'year': year,
        'years_list': [year - 1, year, year + 1],
        'form': form,
    })


@login_required(login_url='login-staff')
def public_holiday_add_view(request):
    """Add a new public holiday."""
    if request.method == 'POST':
        form = PublicHolidayForm(request.POST)
        if form.is_valid():
            holiday = form.save()
            messages.success(request, f"Public Holiday '{holiday.name}' added successfully.")
        else:
            messages.error(request, "Failed to add public holiday. Check date uniqueness.")
    return redirect('leave-holidays')


@login_required(login_url='login-staff')
def public_holiday_toggle_view(request, pk):
    """Toggle active status of a public holiday."""
    if request.method == 'POST':
        holiday = get_object_or_404(PublicHoliday, pk=pk)
        holiday.active = not holiday.active
        holiday.save(update_fields=['active'])
        messages.success(request, f"Holiday '{holiday.name}' set to {'Active' if holiday.active else 'Inactive'}.")
    return redirect('leave-holidays')


@login_required(login_url='login-staff')
def public_holiday_delete_view(request, pk):
    """Delete a public holiday."""
    if request.method == 'POST':
        holiday = get_object_or_404(PublicHoliday, pk=pk)
        name = holiday.name
        holiday.delete()
        messages.success(request, f"Holiday '{name}' deleted.")
    return redirect('leave-holidays')