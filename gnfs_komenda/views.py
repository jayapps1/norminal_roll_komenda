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
    RegionForm, DistrictForm, StationForm, PostForm,
    PersonnelTransferForm, TransferDestinationPlacementForm,
    TransferConfirmArrivalForm, TransferCancelForm, FunctionalRoleAssignmentForm,
    RegionLeadershipForm, DistrictLeadershipForm, DirectorateForm,
    DepartmentForm, UnitForm, PersonnelFunctionalAssignmentCreateForm
)
from gnfs_komenda.models import (
    ApprovalHistory, AuditLog, District, Notification, NorminalRoll,
    PersonnelActionRequest, Post, Rank, Region, Station,
    FunctionalUnit, FunctionalRoleAssignment, FunctionalRoleType,
    PersonnelPosting, PersonnelFunctionalAssignment, PersonnelTransfer,
    TransferType, TransferStatus, TransferApprovalHistory, CommandAppointment,
    AdministrativeAssignment, ApplicationRole, LeaveRequest, LeaveEntitlement
)
from gnfs_komenda.security import (
    can_user_approve_request, can_user_manage_personnel,
    get_districts_queryset_for_user, get_personnel_queryset_for_user,
    get_posts_queryset_for_user, get_regions_queryset_for_user,
    get_stations_queryset_for_user, get_user_assignment, get_user_role
)
from gnfs_komenda.services.transfer_service import (
    create_transfer, submit_transfer, approve_transfer, release_officer,
    mark_reported, complete_destination_placement, confirm_arrival, cancel_transfer
)
from gnfs_komenda.services.permission_resolver import (
    get_command_scope, get_functional_scopes, get_managed_functional_unit_ids,
    get_available_workspaces, get_active_workspace, can_view_personnel as can_view_personnel_resolver,
    can_manage_personnel as can_manage_personnel_resolver, can_manage_functional_unit,
    get_scoped_personnel_queryset, can_manage_regional_leadership,
    can_manage_district_leadership, can_manage_functional_structure
)
from gnfs_komenda.services.posting_service import get_officer_posting_history, create_new_posting
from gnfs_komenda.services.functional_assignment_service import (
    get_officer_functional_history, assign_functional_unit,
    remove_functional_assignment, set_primary_functional_assignment,
    get_active_functional_assignments
)
from gnfs_komenda.services.command_appointment_service import (
    appoint_rfo, appoint_regional_2ic, appoint_commanding_officer,
    appoint_district_2ic, end_command_appointment,
    get_region_leadership_history, get_district_leadership_history,
    get_officer_command_history
)
from gnfs_komenda.services.organisational_structure_service import (
    create_functional_unit, update_functional_unit,
    deactivate_functional_unit, reactivate_functional_unit,
    delete_functional_unit, get_organisation_tree
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

        return queryset.order_by('fullname', 'id')

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

        # Initialize career command posting history
        if staff.station:
            district = staff.station.district if staff.station else None
            region = district.region if district else None
            create_new_posting(
                personnel=staff,
                region=region,
                district=district,
                station=staff.station,
                post=staff.post,
                assigned_by=self.request.user,
                remarks="Initial personnel registration command posting."
            )

        # Initialize functional assignment if selected
        if staff.functional_unit:
            try:
                assign_functional_unit(
                    personnel=staff,
                    functional_unit=staff.functional_unit,
                    assignment_role="Primary Unit",
                    is_primary=True,
                    assigned_by=self.request.user,
                    reason="Initial registration functional assignment."
                )
            except Exception:
                pass

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

        # Provision user account if requested
        if form.cleaned_data.get('create_user_account', True):
            try:
                from staff.services.account_provisioning_service import provision_personnel_account
                provision_personnel_account(staff, created_by=self.request.user)
            except Exception as e:
                messages.warning(self.request, f"Personnel saved, but login account could not be provisioned: {str(e)}")

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
        context['postings'] = staff.postings.all().select_related('region', 'district', 'station', 'post').order_by('-start_date')
        context['current_command_appointment'] = staff.current_command_appointment
        context['command_appointments'] = get_officer_command_history(staff)
        context['active_functional_assignments'] = get_active_functional_assignments(staff)
        context['functional_history'] = get_officer_functional_history(staff)
        context['transfers'] = staff.transfers.all().select_related('source_station', 'destination_station', 'initiated_by').order_by('-created_at')
        context['can_manage_personnel'] = can_user_manage_personnel(self.request.user, staff)
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
    """National Headquarters Dashboard with nationwide visibility (Section 5, 6)."""
    login_url = 'login-staff'

    def get(self, request, *args, **kwargs):
        role = get_user_role(request.user)
        is_national = request.user.is_superuser or role == "NATIONAL_ADMIN"
        if not is_national:
            from gnfs_komenda.services.role_service import get_actor_administrative_scope
            admin_scope = get_actor_administrative_scope(request.user)
            if admin_scope["level"] != "NATIONAL":
                messages.error(request, "Access restricted to National Administrators.")
                return redirect('my-dashboard')

        NorminalRoll.sync_leaves()

        # Database Aggregate Metrics (12 Summary Cards)
        total_personnel = NorminalRoll.active_objects.count()
        senior_officers = NorminalRoll.active_objects.filter(officer_category=Rank.OfficerCategory.SENIOR).count()
        junior_officers = NorminalRoll.active_objects.filter(officer_category=Rank.OfficerCategory.JUNIOR).count()
        total_regions = Region.objects.filter(active=True).count()
        total_districts = District.objects.filter(active=True).count()
        total_stations = Station.objects.filter(active=True).count()
        total_posts = Post.objects.filter(active=True).count()
        personnel_at_post = NorminalRoll.active_objects.filter(leave_status="AT POST").count()
        personnel_on_leave = NorminalRoll.active_objects.filter(leave_status="ON LEAVE").count()

        pending_leave = LeaveRequest.objects.filter(
            status__in=[
                LeaveRequest.Status.PENDING_PROCESSING,
                LeaveRequest.Status.PENDING_APPROVAL,
            ]
        ).count()

        pending_transfers = PersonnelTransfer.objects.filter(
            status__in=[
                TransferStatus.PENDING_APPROVAL,
                TransferStatus.APPROVED
            ]
        ).count()

        awaiting_reporting = PersonnelTransfer.objects.filter(
            status=TransferStatus.AWAITING_REPORTING
        ).count()

        pending_approvals = PersonnelActionRequest.objects.filter(status="PENDING_NATIONAL").count()

        # 8 National Overview Sections
        # 1. Regional Strength
        regions = Region.objects.filter(active=True).annotate(
            district_count=Count('districts', distinct=True),
            station_count=Count('districts__stations', distinct=True),
            post_count=Count('districts__stations__posts', distinct=True),
            personnel_count=Count('districts__stations__personnel', filter=Q(districts__stations__personnel__is_active=True), distinct=True)
        ).order_by('name')

        # 2. Personnel Distribution
        male_count = NorminalRoll.active_objects.filter(gender="MALE").count()
        female_count = NorminalRoll.active_objects.filter(gender="FEMALE").count()
        top_ranks = Rank.objects.annotate(
            officer_count=Count('personnel', filter=Q(personnel__is_active=True))
        ).filter(officer_count__gt=0).order_by('-hierarchy_order')[:8]

        # 3. Leave Overview
        leave_stats = {
            'pending_processing': LeaveRequest.objects.filter(status=LeaveRequest.Status.PENDING_PROCESSING).count(),
            'pending_approval': LeaveRequest.objects.filter(status=LeaveRequest.Status.PENDING_APPROVAL).count(),
            'approved_active': LeaveRequest.objects.filter(status=LeaveRequest.Status.APPROVED).count(),
            'recent_leaves': LeaveRequest.objects.select_related('personnel', 'leave_type').order_by('-created_at')[:8],
        }

        # 4. Transfer Overview
        transfer_stats = {
            'pending': pending_transfers,
            'released': PersonnelTransfer.objects.filter(status=TransferStatus.RELEASED).count(),
            'awaiting_reporting': awaiting_reporting,
            'confirmed': PersonnelTransfer.objects.filter(status=TransferStatus.CONFIRMED).count(),
            'recent_transfers': PersonnelTransfer.objects.select_related('personnel', 'source_station', 'destination_station', 'destination_region').order_by('-created_at')[:8],
        }

        # 5. Administrative Assignments
        admin_assignments = AdministrativeAssignment.objects.filter(is_active=True).select_related(
            'personnel', 'role', 'region', 'district', 'assigned_by'
        ).order_by('-assigned_at')[:10]

        # 6. Command Leadership
        command_leadership = CommandAppointment.objects.filter(is_active=True).select_related(
            'personnel', 'region', 'district'
        ).order_by('appointment_type', 'region__name', 'district__name')[:12]

        # 7. Recent System Activity
        recent_activity = AuditLog.objects.select_related('user').order_by('-timestamp')[:10]

        # 8. Alerts
        alerts = []
        if awaiting_reporting > 0:
            alerts.append({
                'type': 'warning',
                'icon': 'fas fa-plane-arrival',
                'title': 'Officers Awaiting Reporting',
                'message': f"{awaiting_reporting} transferred personnel released and awaiting arrival confirmation at destination.",
                'url': reverse('transfer-list')
            })
        if pending_leave > 0:
            alerts.append({
                'type': 'info',
                'icon': 'fas fa-calendar-check',
                'title': 'Pending Leave Workflow',
                'message': f"{pending_leave} leave requests awaiting administrative processing or command decision.",
                'url': reverse('leave-dashboard')
            })
        if pending_transfers > 0:
            alerts.append({
                'type': 'primary',
                'icon': 'fas fa-exchange-alt',
                'title': 'Pending Transfer Authorisations',
                'message': f"{pending_transfers} transfer applications requiring review.",
                'url': reverse('transfer-list')
            })

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
            "pending_leave": pending_leave,
            "pending_transfers": pending_transfers,
            "awaiting_reporting": awaiting_reporting,
            "pending_approvals": pending_approvals,
            "regions": regions,
            "male_count": male_count,
            "female_count": female_count,
            "top_ranks": top_ranks,
            "leave_stats": leave_stats,
            "transfer_stats": transfer_stats,
            "admin_assignments": admin_assignments,
            "command_leadership": command_leadership,
            "recent_activity": recent_activity,
            "alerts": alerts,
        })


class RegionalDashboardView(LoginRequiredMixin, View):
    """Regional Administration Dashboard scoped to the specified region (Section 13-15)."""
    login_url = 'login-staff'

    def get(self, request, region_id, *args, **kwargs):
        region = get_object_or_404(Region, id=region_id, active=True)
        role = get_user_role(request.user)
        assignment = get_user_assignment(request.user)

        user_personnel = NorminalRoll.objects.filter(
            staffID__iexact=request.user.username
        ).first()

        is_regional_leader = False
        if user_personnel:
            if (region.current_rfo and region.current_rfo.personnel_id == user_personnel.id) or \
               (region.current_2ic and region.current_2ic.personnel_id == user_personnel.id):
                is_regional_leader = True

        from gnfs_komenda.services.role_service import get_actor_administrative_scope
        admin_scope = get_actor_administrative_scope(request.user)
        is_regional_admin = (admin_scope["level"] == "REGIONAL" and admin_scope["region"] and admin_scope["region"].id == region.id)
        is_super = request.user.is_superuser or role == "NATIONAL_ADMIN" or admin_scope["level"] == "NATIONAL"

        if not (is_super or is_regional_admin or is_regional_leader):
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

        leave_awaiting_processing = LeaveRequest.objects.filter(
            routing_region=region,
            status=LeaveRequest.Status.PENDING_PROCESSING,
            processing_stage=LeaveRequest.ProcessingStage.REGIONAL_ADMIN
        ).count()

        leave_awaiting_approval = LeaveRequest.objects.filter(
            routing_region=region,
            status=LeaveRequest.Status.PENDING_APPROVAL,
            processing_stage=LeaveRequest.ProcessingStage.COMMAND_APPROVAL
        ).count()

        incoming_transfers = PersonnelTransfer.objects.filter(
            destination_region=region,
            status__in=[TransferStatus.APPROVED, TransferStatus.RELEASED]
        ).count()

        outgoing_transfers = PersonnelTransfer.objects.filter(
            source_region=region,
            status__in=[TransferStatus.PENDING_APPROVAL, TransferStatus.APPROVED, TransferStatus.RELEASED]
        ).count()

        district_administrators = AdministrativeAssignment.objects.filter(
            region=region,
            role__code="DISTRICT_ADMIN",
            is_active=True
        ).select_related('personnel', 'district', 'assigned_by')

        pending_regional_approvals = PersonnelActionRequest.objects.filter(
            status="PENDING_REGIONAL",
            personnel__station__district__region=region
        ).count()

        return render(request, "gnfs_komenda/dashboard_regional.html", {
            "region": region,
            "current_rfo": region.current_rfo,
            "current_2ic": region.current_2ic,
            "can_manage": can_manage_regional_leadership(request.user, region),
            "is_regional_admin": is_regional_admin or is_super,
            "total_personnel": total_personnel,
            "senior_officers": senior_officers,
            "junior_officers": junior_officers,
            "at_post": at_post,
            "on_leave": on_leave,
            "district_count": districts.count(),
            "station_count": Station.objects.filter(district__region=region, active=True).count(),
            "post_count": Post.objects.filter(station__district__region=region, active=True).count(),
            "leave_awaiting_processing": leave_awaiting_processing,
            "leave_awaiting_approval": leave_awaiting_approval,
            "incoming_transfers": incoming_transfers,
            "outgoing_transfers": outgoing_transfers,
            "district_administrators": district_administrators,
            "pending_approvals": pending_regional_approvals,
            "districts": districts,
        })


class RegionalCommandDashboardView(LoginRequiredMixin, View):
    """RFO & Regional 2IC Command Dashboard (Section 26-28, 35)."""
    login_url = 'login-staff'

    def get(self, request, region_id, *args, **kwargs):
        region = get_object_or_404(Region, id=region_id, active=True)
        role = get_user_role(request.user)
        user_personnel = NorminalRoll.objects.filter(staffID__iexact=request.user.username).first()

        is_rfo = user_personnel and region.current_rfo and region.current_rfo.personnel_id == user_personnel.id
        is_2ic = user_personnel and region.current_2ic and region.current_2ic.personnel_id == user_personnel.id
        is_super = request.user.is_superuser or role == "NATIONAL_ADMIN"

        if not (is_super or is_rfo or is_2ic):
            raise PermissionDenied("Access restricted to Regional Command Officers.")

        NorminalRoll.sync_leaves()

        scoped_personnel = NorminalRoll.active_objects.filter(station__district__region=region)
        total_personnel = scoped_personnel.count()
        senior_officers = scoped_personnel.filter(officer_category=Rank.OfficerCategory.SENIOR).count()
        junior_officers = scoped_personnel.filter(officer_category=Rank.OfficerCategory.JUNIOR).count()
        at_post = scoped_personnel.filter(leave_status="AT POST").count()
        on_leave = scoped_personnel.filter(leave_status="ON LEAVE").count()

        leave_awaiting_approval = LeaveRequest.objects.filter(
            routing_region=region,
            status=LeaveRequest.Status.PENDING_APPROVAL,
            processing_stage=LeaveRequest.ProcessingStage.COMMAND_APPROVAL
        ).count()

        dfo_leave_requests = LeaveRequest.objects.filter(
            routing_region=region,
            status=LeaveRequest.Status.PENDING_APPROVAL,
            processing_stage=LeaveRequest.ProcessingStage.COMMAND_APPROVAL
        ).select_related('personnel', 'leave_type')[:8]

        incoming_transfers = PersonnelTransfer.objects.filter(
            destination_region=region,
            status__in=[TransferStatus.APPROVED, TransferStatus.RELEASED, TransferStatus.AWAITING_REPORTING]
        ).count()

        reporting_overdue = PersonnelTransfer.objects.filter(
            destination_region=region,
            status=TransferStatus.AWAITING_REPORTING,
            expected_reporting_date__lt=timezone.localdate()
        ).count()

        districts = District.objects.filter(region=region, active=True).annotate(
            personnel_count=Count('stations__personnel', filter=Q(stations__personnel__is_active=True), distinct=True)
        ).order_by('name')

        return render(request, "gnfs_komenda/dashboard_rfo.html", {
            "region": region,
            "current_rfo": region.current_rfo,
            "current_2ic": region.current_2ic,
            "is_rfo": is_rfo or is_super,
            "is_2ic": is_2ic,
            "total_personnel": total_personnel,
            "senior_officers": senior_officers,
            "junior_officers": junior_officers,
            "at_post": at_post,
            "on_leave": on_leave,
            "district_count": districts.count(),
            "leave_awaiting_approval": leave_awaiting_approval,
            "dfo_leave_requests": dfo_leave_requests,
            "incoming_transfers": incoming_transfers,
            "reporting_overdue": reporting_overdue,
            "districts": districts,
        })


class DistrictDashboardView(LoginRequiredMixin, View):
    """District Administration Dashboard scoped to the specified district (Section 19-22)."""
    login_url = 'login-staff'

    def get(self, request, district_id, *args, **kwargs):
        district = get_object_or_404(District, id=district_id, active=True)
        role = get_user_role(request.user)
        assignment = get_user_assignment(request.user)

        user_personnel = NorminalRoll.objects.filter(
            staffID__iexact=request.user.username
        ).first()

        is_district_leader = False
        if user_personnel:
            if (district.current_commanding_officer and district.current_commanding_officer.personnel_id == user_personnel.id) or \
               (district.current_2ic and district.current_2ic.personnel_id == user_personnel.id) or \
               (district.region and district.region.current_rfo and district.region.current_rfo.personnel_id == user_personnel.id):
                is_district_leader = True

        from gnfs_komenda.services.role_service import get_actor_administrative_scope
        admin_scope = get_actor_administrative_scope(request.user)
        is_district_admin = (admin_scope["level"] == "DISTRICT" and admin_scope["district"] and admin_scope["district"].id == district.id)
        is_regional_admin = (admin_scope["level"] == "REGIONAL" and admin_scope["region"] and admin_scope["region"].id == district.region_id)
        is_super = request.user.is_superuser or role == "NATIONAL_ADMIN" or admin_scope["level"] == "NATIONAL"

        if not (is_super or is_regional_admin or is_district_admin or is_district_leader):
            if role == "REGIONAL_ADMIN" and assignment and assignment.region_id != district.region_id:
                raise PermissionDenied("You are not authorized to access districts outside your region.")
            elif role in ("DFO", "DISTRICT_ADMIN") and assignment and assignment.district_id != district.id:
                raise PermissionDenied("You are not authorized to access another District.")
            else:
                raise PermissionDenied("You are not authorized to access this District console.")

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

        leave_awaiting_processing = LeaveRequest.objects.filter(
            routing_district=district,
            status=LeaveRequest.Status.PENDING_PROCESSING,
            processing_stage=LeaveRequest.ProcessingStage.DISTRICT_ADMIN
        ).count()

        incoming_transfers = PersonnelTransfer.objects.filter(
            destination_district=district,
            status__in=[TransferStatus.APPROVED, TransferStatus.RELEASED]
        ).count()

        outgoing_transfers = PersonnelTransfer.objects.filter(
            source_district=district,
            status__in=[TransferStatus.PENDING_APPROVAL, TransferStatus.APPROVED, TransferStatus.RELEASED]
        ).count()

        recent_activity = AuditLog.objects.filter(
            Q(district=district) | Q(description__icontains=district.name)
        ).order_by('-timestamp')[:8]

        pending_dfo_approvals = PersonnelActionRequest.objects.filter(
            status="PENDING_DFO",
            personnel__station__district=district
        ).count()

        return render(request, "gnfs_komenda/dashboard_district.html", {
            "district": district,
            "region": district.region,
            "current_co": district.current_commanding_officer,
            "current_2ic": district.current_2ic,
            "can_manage": can_manage_district_leadership(request.user, district),
            "is_district_admin": is_district_admin or is_super,
            "total_personnel": total_personnel,
            "senior_officers": senior_officers,
            "junior_officers": junior_officers,
            "at_post": at_post,
            "on_leave": on_leave,
            "station_count": stations.count(),
            "post_count": Post.objects.filter(station__district=district, active=True).count(),
            "leave_awaiting_processing": leave_awaiting_processing,
            "incoming_transfers": incoming_transfers,
            "outgoing_transfers": outgoing_transfers,
            "recent_activity": recent_activity,
            "pending_approvals": pending_dfo_approvals,
            "stations": stations,
        })


class DistrictCommandDashboardView(LoginRequiredMixin, View):
    """
    DFO / MFO & District 2IC Command Dashboard (Prompt 2 Section 13, 14).
    DFO/MFO receives command decision authority.
    2IC receives command overview and visibility into approved leave for command awareness,
    without automatic DFO/MFO final approval authority.
    """
    login_url = 'login-staff'

    def get(self, request, district_id, *args, **kwargs):
        district = get_object_or_404(District, id=district_id, active=True)
        role = get_user_role(request.user)
        user_personnel = NorminalRoll.objects.filter(staffID__iexact=request.user.username).first()

        is_co = user_personnel and district.current_commanding_officer and district.current_commanding_officer.personnel_id == user_personnel.id and district.current_commanding_officer.is_active
        is_2ic = user_personnel and district.current_2ic and district.current_2ic.personnel_id == user_personnel.id and district.current_2ic.is_active
        is_super = request.user.is_superuser or role == "NATIONAL_ADMIN"

        # Also check CommandAppointment records
        if user_personnel and not (is_co or is_2ic):
            active_cmd = user_personnel.command_appointments.filter(district=district, is_active=True).first()
            if active_cmd:
                if active_cmd.appointment_type in ('DFO', 'MFO', 'METRO_FO', 'ACTING_DFO', 'ACTING_MFO', 'ACTING_METRO_FO'):
                    is_co = True
                elif active_cmd.appointment_type in ('DISTRICT_2IC', 'ACTING_DISTRICT_2IC'):
                    is_2ic = True

        # Also check AdministrativeAssignment records
        if user_personnel and not (is_co or is_2ic):
            from gnfs_komenda.models import AdministrativeAssignment
            admin_assigns = AdministrativeAssignment.objects.filter(
                personnel=user_personnel,
                district=district,
                is_active=True
            ).select_related('role')
            for a in admin_assigns:
                rcode = a.role.code if a.role else ''
                rtitle = (a.responsibility_title or '').upper()
                if rcode in ('DFO', 'MFO', 'COMMANDING_OFFICER') or 'COMMANDING' in rtitle or 'DFO' in rtitle or 'MFO' in rtitle:
                    is_co = True
                    break
                elif rcode in ('DISTRICT_2IC', '2IC') or '2IC' in rtitle or 'SECOND IN COMMAND' in rtitle:
                    is_2ic = True

        if not (is_super or is_co or is_2ic):
            raise PermissionDenied("Access restricted to District Command Officers.")

        NorminalRoll.sync_leaves()

        scoped_personnel = NorminalRoll.active_objects.filter(station__district=district)
        total_personnel = scoped_personnel.count()

        # Operational Strength (Section 13)
        ops_qs = scoped_personnel.filter(
            Q(functional_assignments__functional_unit__name__icontains="Operations") |
            Q(functional_assignments__functional_office__function_type="OPERATIONS")
        ).distinct()
        if not ops_qs.exists():
            non_ops = scoped_personnel.filter(
                Q(functional_assignments__functional_unit__name__iregex=r'(Accounts|Finance|Audit|Human Resource|HR|Legal|Welfare)') |
                Q(functional_assignments__functional_office__function_type__in=["ACCOUNTS", "HR"])
            ).distinct()
            ops_qs = scoped_personnel.exclude(id__in=non_ops.values_list('id', flat=True))
        operational_strength = ops_qs.count()

        senior_officers = scoped_personnel.filter(officer_category=Rank.OfficerCategory.SENIOR).count()
        junior_officers = scoped_personnel.filter(officer_category=Rank.OfficerCategory.JUNIOR).count()
        at_post = scoped_personnel.filter(leave_status="AT POST").count()
        on_leave = scoped_personnel.filter(leave_status="ON LEAVE").count()

        # Decision metrics (Section 13 & 21)
        district_leaves = LeaveRequest.objects.filter(routing_district=district)
        leave_awaiting_approval = district_leaves.filter(
            status__in=[LeaveRequest.Status.PENDING_DFO_MFO_DECISION, LeaveRequest.Status.PENDING_APPROVAL]
        ).count()
        approved_leave = district_leaves.filter(
            status__in=[
                LeaveRequest.Status.APPROVED,
                LeaveRequest.Status.APPROVED_PENDING_ADMIN_PROCESSING,
                LeaveRequest.Status.PENDING_ADMIN_FINAL_PROCESSING,
                LeaveRequest.Status.SENT_TO_WATCH_ROOM,
                LeaveRequest.Status.BOOKED,
                LeaveRequest.Status.ACTIVE,
            ]
        ).count()
        rejected_leave = district_leaves.filter(status=LeaveRequest.Status.REJECTED).count()

        today = timezone.localdate()
        approved_today = district_leaves.filter(
            status__in=[
                LeaveRequest.Status.APPROVED,
                LeaveRequest.Status.APPROVED_PENDING_ADMIN_PROCESSING,
                LeaveRequest.Status.PENDING_ADMIN_FINAL_PROCESSING,
                LeaveRequest.Status.SENT_TO_WATCH_ROOM,
                LeaveRequest.Status.BOOKED,
                LeaveRequest.Status.ACTIVE,
            ],
            approved_at__date=today
        ).count()
        rejected_today = district_leaves.filter(
            status=LeaveRequest.Status.REJECTED,
            updated_at__date=today
        ).count()
        ops_on_leave = ops_qs.filter(leave_status="ON LEAVE").count()

        incoming_transfers = PersonnelTransfer.objects.filter(
            destination_district=district,
            status__in=[TransferStatus.APPROVED, TransferStatus.RELEASED]
        ).count()

        outgoing_transfers = PersonnelTransfer.objects.filter(
            source_district=district,
            status__in=[TransferStatus.PENDING_APPROVAL, TransferStatus.APPROVED, TransferStatus.RELEASED]
        ).count()
        total_transfers = incoming_transfers + outgoing_transfers

        from datetime import timedelta
        resuming_soon = NorminalRoll.active_objects.filter(
            station__district=district,
            leave_status="ON LEAVE",
            end_leave__gte=today,
            end_leave__lte=today + timedelta(days=7)
        ).count()

        notifications_count = Notification.objects.filter(recipient=request.user, is_read=False).count()

        stations = Station.objects.filter(district=district, active=True).annotate(
            personnel_count=Count('personnel', filter=Q(personnel__is_active=True), distinct=True)
        ).order_by('name')

        # Detailed leave queues: decision inbox for DFO/MFO, approved leaves for 2IC command awareness
        decision_leaves = district_leaves.filter(
            status__in=[LeaveRequest.Status.PENDING_DFO_MFO_DECISION, LeaveRequest.Status.PENDING_APPROVAL]
        ).select_related('personnel', 'personnel__rank', 'personnel__station', 'leave_type').order_by('-updated_at')[:15]

        approved_leaves = district_leaves.filter(
            status__in=[
                LeaveRequest.Status.APPROVED,
                LeaveRequest.Status.APPROVED_PENDING_ADMIN_PROCESSING,
                LeaveRequest.Status.PENDING_ADMIN_FINAL_PROCESSING,
                LeaveRequest.Status.SENT_TO_WATCH_ROOM,
                LeaveRequest.Status.BOOKED,
                LeaveRequest.Status.ACTIVE,
            ]
        ).select_related('personnel', 'personnel__rank', 'personnel__station', 'leave_type', 'approved_by').order_by('-approved_at')[:15]

        # 2IC does NOT automatically receive DFO/MFO approval permission
        can_approve = is_co or is_super

        return render(request, "gnfs_komenda/dashboard_dfo.html", {
            "district": district,
            "region": district.region,
            "current_co": district.current_commanding_officer,
            "current_2ic": district.current_2ic,
            "is_co": is_co or is_super,
            "is_2ic": is_2ic,
            "can_approve": can_approve,
            "total_personnel": total_personnel,
            "operational_strength": operational_strength,
            "senior_officers": senior_officers,
            "junior_officers": junior_officers,
            "at_post": at_post,
            "on_leave": on_leave,
            "leave_awaiting_approval": leave_awaiting_approval,
            "approved_leave": approved_leave,
            "rejected_leave": rejected_leave,
            "approved_today": approved_today,
            "rejected_today": rejected_today,
            "ops_on_leave": ops_on_leave,
            "incoming_transfers": incoming_transfers,
            "outgoing_transfers": outgoing_transfers,
            "total_transfers": total_transfers,
            "resuming_soon": resuming_soon,
            "notifications_count": notifications_count,
            "stations": stations,
            "decision_leaves": decision_leaves,
            "approved_leaves": approved_leaves,
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
            "profile_picture_url": staff.profile_picture_url,
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
def leave_set_proportionate_entitlement_view(request, entitlement_id):
    """
    Authorised administrator view to configure Proportionate / Prorated Annual Leave Entitlement (Section 23).
    Separates proportionate entitlement from partial leave requests.
    Enforces strict backend validation and audited record keeping.
    """
    from gnfs_komenda.forms import ProportionateLeaveEntitlementForm
    from gnfs_komenda.services.leave_policy_service import (
        can_manage_proportionate_entitlement,
        set_proportionate_annual_entitlement,
        restore_standard_annual_entitlement,
        get_base_annual_entitlement,
    )

    entitlement = get_object_or_404(
        LeaveEntitlement.objects.select_related('personnel', 'personnel__rank', 'leave_type'),
        pk=entitlement_id
    )

    if not can_manage_proportionate_entitlement(request.user, entitlement.personnel):
        raise PermissionDenied("You do not have administrative authority to configure this personnel's leave entitlement.")

    standard_days = entitlement.full_annual_entitlement or get_base_annual_entitlement(entitlement.personnel.officer_category)

    if request.method == 'POST':
        form = ProportionateLeaveEntitlementForm(request.POST, entitlement=entitlement, user=request.user)
        if form.is_valid():
            ent_type = form.cleaned_data['entitlement_type']
            days = form.cleaned_data.get('proportionate_days')
            reason = form.cleaned_data.get('reason')
            eff_date = form.cleaned_data.get('effective_date')

            try:
                if ent_type == ProportionateLeaveEntitlementForm.EntitlementType.PROPORTIONATE:
                    set_proportionate_annual_entitlement(entitlement, days, reason, request.user, eff_date)
                    messages.success(request, f"Proportionate annual leave of {days} working days set for {entitlement.personnel.fullname}.")
                else:
                    restore_standard_annual_entitlement(entitlement, reason or "Restored standard entitlement.", request.user)
                    messages.success(request, f"Standard full annual leave of {standard_days} working days restored for {entitlement.personnel.fullname}.")

                next_url = request.POST.get('next') or request.GET.get('next')
                if next_url:
                    return redirect(next_url)
                return redirect('leave-balances')
            except ValidationError as e:
                messages.error(request, str(e.message if hasattr(e, 'message') else e))
        else:
            for field, errs in form.errors.items():
                messages.error(request, f"{field.replace('_', ' ').title()}: {', '.join(errs)}")
    else:
        form = ProportionateLeaveEntitlementForm(entitlement=entitlement, user=request.user)

    return render(request, 'gnfs_komenda/leave/proportionate_form.html', {
        'entitlement': entitlement,
        'standard_days': standard_days,
        'form': form,
    })


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


@login_required(login_url='login-staff')
def leave_policies_list_view(request):
    """
    National Leave Policies Management Dashboard (Section 20 & 21).
    Super Admin / National Admin view showing Core and Custom policies.
    """
    from gnfs_komenda.models import LeavePolicy
    from gnfs_komenda.services.leave_policy_service import seed_core_leave_policies
    if not LeavePolicy.objects.exists():
        seed_core_leave_policies()

    core_policies = LeavePolicy.objects.filter(is_core_policy=True).select_related('leave_type')
    custom_policies = LeavePolicy.objects.filter(is_core_policy=False).select_related('leave_type')

    return render(request, "gnfs_komenda/leave/policies.html", {
        "core_policies": core_policies,
        "custom_policies": custom_policies,
    })



# ==============================================================================
# FUNCTIONAL STRUCTURE CASCADING APIS & WORKSPACE SWITCHER
# ==============================================================================

@login_required(login_url='login-staff')
def api_directorates(request):
    """Return JSON list of top-level Directorates."""
    qs = FunctionalUnit.objects.filter(parent__isnull=True, active=True).order_by('display_order', 'name')
    data = [{"id": u.id, "name": u.name, "code": u.code} for u in qs]
    return JsonResponse({"directorates": data})


@login_required(login_url='login-staff')
def api_functional_children(request):
    """Return child functional units for a given parent_id."""
    parent_id = request.GET.get("parent_id")
    if not parent_id:
        return JsonResponse({"children": []})
    qs = FunctionalUnit.objects.filter(parent_id=parent_id, active=True).order_by('display_order', 'name')
    data = [
        {
            "id": u.id,
            "name": u.name,
            "code": u.code,
            "unit_type": u.unit_type,
            "unit_type_display": u.get_unit_type_display(),
            "has_children": u.children.filter(active=True).exists(),
        }
        for u in qs
    ]
    return JsonResponse({"children": data})


@login_required(login_url='login-staff')
def switch_workspace_view(request, workspace_id):
    """Switch active management workspace in session without changing backend permissions."""
    workspaces = get_available_workspaces(request.user)
    valid_ids = [w['id'] for w in workspaces]
    if workspace_id in valid_ids:
        request.session['gnfs_active_workspace_id'] = workspace_id
        target_ws = next((w for w in workspaces if w['id'] == workspace_id), None)
        messages.success(request, f"Switched to workspace: {target_ws['label'] if target_ws else workspace_id}")
        if target_ws and target_ws.get('url'):
            return redirect(target_ws['url'])
    else:
        messages.error(request, "Selected workspace is not authorized for your account.")
    return redirect('personnel')


class FunctionalDashboardView(LoginRequiredMixin, View):
    """Scope-aware dashboard for Directorate, Department, or Unit Heads."""
    login_url = 'login-staff'

    def get(self, request, unit_id, *args, **kwargs):
        unit = get_object_or_404(FunctionalUnit, id=unit_id, active=True)
        if not can_manage_functional_unit(request.user, unit):
            raise PermissionDenied(f"You do not have management access for {unit.name}.")

        NorminalRoll.sync_leaves()

        # Update active workspace to match this dashboard
        request.session['gnfs_active_workspace_id'] = f"func_{unit.id}"

        descendant_ids = unit.get_descendant_ids(include_self=True)
        personnel = NorminalRoll.active_objects.filter(functional_unit_id__in=descendant_ids).select_related('rank', 'station', 'functional_unit')

        total_personnel = personnel.count()
        senior_officers = personnel.filter(officer_category=Rank.OfficerCategory.SENIOR).count()
        junior_officers = personnel.filter(officer_category=Rank.OfficerCategory.JUNIOR).count()
        at_post = personnel.filter(leave_status="AT POST").count()
        on_leave = personnel.filter(leave_status="ON LEAVE").count()

        # Children breakdown
        children = unit.children.filter(active=True).annotate(
            personnel_count=Count('personnel', filter=Q(personnel__is_active=True), distinct=True)
        ).order_by('display_order', 'name')

        # Personnel by rank
        ranks_breakdown = personnel.values('rank__name', 'rank__abbreviation').annotate(
            count=Count('id')
        ).order_by('-count')

        # Pending leave requests of officers under this functional umbrella
        from gnfs_komenda.models import LeaveRequest
        pending_leaves = LeaveRequest.objects.filter(
            personnel__in=personnel,
            status='PENDING_APPROVAL'
        ).select_related('personnel', 'leave_type')

        return render(request, "gnfs_komenda/dashboard_functional.html", {
            "unit": unit,
            "unit_type": unit.unit_type,
            "total_personnel": total_personnel,
            "senior_officers": senior_officers,
            "junior_officers": junior_officers,
            "at_post": at_post,
            "on_leave": on_leave,
            "children": children,
            "ranks_breakdown": ranks_breakdown,
            "pending_leaves": pending_leaves,
            "personnel": personnel[:30],
        })


# ==============================================================================
# PERSONNEL TRANSFER & POSTING MANAGEMENT VIEWS
# ==============================================================================

@login_required(login_url='login-staff')
def transfer_list_view(request):
    """Hub for outgoing, incoming, and historical transfers."""
    user = request.user
    cmd_scope = get_command_scope(user)
    level = cmd_scope.get("level")

    base_qs = PersonnelTransfer.objects.select_related(
        'personnel', 'personnel__rank', 'source_region', 'source_district',
        'source_station', 'destination_region', 'destination_district',
        'destination_station', 'initiated_by'
    )

    if user.is_superuser or get_user_role(user) == "NATIONAL_ADMIN":
        outgoing = base_qs.all()
        incoming = base_qs.all()
    elif level == "REGIONAL" and cmd_scope.get("region"):
        r = cmd_scope["region"]
        outgoing = base_qs.filter(source_region=r)
        incoming = base_qs.filter(destination_region=r)
    elif level in ("DISTRICT",) and cmd_scope.get("district"):
        d = cmd_scope["district"]
        outgoing = base_qs.filter(source_district=d)
        incoming = base_qs.filter(destination_district=d)
    elif level == "STATION" and cmd_scope.get("station"):
        s = cmd_scope["station"]
        outgoing = base_qs.filter(source_station=s)
        incoming = base_qs.filter(destination_station=s)
    else:
        outgoing = base_qs.none()
        incoming = base_qs.none()

    active_incoming = incoming.filter(status__in=[TransferStatus.RELEASED, TransferStatus.REPORTED])
    awaiting_reporting = incoming.filter(status=TransferStatus.RELEASED)
    today = timezone.now().date()
    overdue_count = awaiting_reporting.filter(expected_reporting_date__lt=today).count()

    return render(request, "gnfs_komenda/transfers/list.html", {
        "outgoing_transfers": outgoing.order_by('-created_at')[:25],
        "incoming_transfers": active_incoming.order_by('-created_at'),
        "all_incoming": incoming.order_by('-created_at')[:25],
        "awaiting_reporting_count": awaiting_reporting.count(),
        "reported_count": incoming.filter(status=TransferStatus.REPORTED).count(),
        "overdue_count": overdue_count,
        "completed_count": incoming.filter(status=TransferStatus.CONFIRMED).count(),
    })


@login_required(login_url='login-staff')
def transfer_create_view(request, personnel_id):
    """Initiate a transfer for an officer."""
    personnel = get_object_or_404(NorminalRoll, id=personnel_id)
    if not can_manage_personnel_resolver(request.user, personnel):
        raise PermissionDenied("You do not have authority to transfer this personnel.")

    if request.method == 'POST':
        form = PersonnelTransferForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                transfer = create_transfer(
                    personnel=personnel,
                    user=request.user,
                    destination_region=form.cleaned_data['destination_region'],
                    destination_district=form.cleaned_data.get('destination_district'),
                    destination_station=form.cleaned_data.get('destination_station'),
                    destination_post=form.cleaned_data.get('destination_post'),
                    destination_functional_unit=form.cleaned_data.get('destination_functional_unit'),
                    transfer_letter_number=form.cleaned_data.get('transfer_letter_number'),
                    transfer_letter_date=form.cleaned_data.get('transfer_letter_date'),
                    effective_date=form.cleaned_data.get('effective_date'),
                    expected_reporting_date=form.cleaned_data.get('expected_reporting_date'),
                    reason=form.cleaned_data.get('reason'),
                    remarks=form.cleaned_data.get('remarks'),
                    attachment=request.FILES.get('attachment'),
                    submit_now=True
                )
                messages.success(request, f"Transfer {transfer.reference_number} created and submitted for approval.")
                return redirect('transfer-detail', transfer_id=transfer.id)
            except ValidationError as e:
                form.add_error(None, e.message if hasattr(e, 'message') else str(e))
    else:
        form = PersonnelTransferForm()

    return render(request, "gnfs_komenda/transfers/create.html", {
        "personnel": personnel,
        "form": form,
    })


@login_required(login_url='login-staff')
def transfer_detail_view(request, transfer_id):
    """View full transfer dossier and action workflow."""
    transfer = get_object_or_404(PersonnelTransfer, id=transfer_id)
    history = transfer.history.all().order_by('-timestamp')
    can_release = transfer.status in (TransferStatus.APPROVED, TransferStatus.PENDING_APPROVAL)
    can_confirm = transfer.status in (TransferStatus.RELEASED, TransferStatus.REPORTED)
    can_cancel = transfer.status not in (TransferStatus.CONFIRMED, TransferStatus.CANCELLED)

    return render(request, "gnfs_komenda/transfers/detail.html", {
        "transfer": transfer,
        "history": history,
        "can_release": can_release,
        "can_confirm": can_confirm,
        "can_cancel": can_cancel,
    })


@login_required(login_url='login-staff')
def transfer_approve_view(request, transfer_id):
    """Approve a pending transfer."""
    transfer = get_object_or_404(PersonnelTransfer, id=transfer_id)
    if request.method == 'POST':
        comments = request.POST.get('comments', '')
        try:
            approve_transfer(transfer, request.user, comments=comments)
            messages.success(request, f"Transfer {transfer.reference_number} approved.")
        except ValidationError as e:
            messages.error(request, str(e))
    return redirect('transfer-detail', transfer_id=transfer.id)


@login_required(login_url='login-staff')
def transfer_release_view(request, transfer_id):
    """Release officer from source command."""
    transfer = get_object_or_404(PersonnelTransfer, id=transfer_id)
    if request.method == 'POST':
        comments = request.POST.get('comments', '')
        try:
            release_officer(transfer, request.user, comments=comments)
            messages.success(request, f"Officer {transfer.personnel.fullname} released. Status: Awaiting Reporting.")
        except ValidationError as e:
            messages.error(request, str(e))
    return redirect('transfer-detail', transfer_id=transfer.id)


@login_required(login_url='login-staff')
def transfer_mark_reported_view(request, transfer_id):
    """Mark that officer has reported at destination."""
    transfer = get_object_or_404(PersonnelTransfer, id=transfer_id)
    if request.method == 'POST':
        comments = request.POST.get('comments', '')
        try:
            mark_reported(transfer, request.user, comments=comments)
            messages.success(request, f"Officer {transfer.personnel.fullname} marked as reported at destination.")
        except ValidationError as e:
            messages.error(request, str(e))
    return redirect('transfer-detail', transfer_id=transfer.id)


@login_required(login_url='login-staff')
def transfer_complete_placement_view(request, transfer_id):
    """Receiving administration assigns destination district/station/post."""
    transfer = get_object_or_404(PersonnelTransfer, id=transfer_id)
    if request.method == 'POST':
        form = TransferDestinationPlacementForm(request.POST, instance=transfer)
        if form.is_valid():
            try:
                complete_destination_placement(
                    transfer,
                    request.user,
                    destination_district=form.cleaned_data['destination_district'],
                    destination_station=form.cleaned_data['destination_station'],
                    destination_post=form.cleaned_data.get('destination_post'),
                    comments=form.cleaned_data.get('remarks')
                )
                messages.success(request, "Destination placement updated successfully.")
                return redirect('transfer-detail', transfer_id=transfer.id)
            except ValidationError as e:
                form.add_error(None, str(e))
    else:
        form = TransferDestinationPlacementForm(instance=transfer)

    return render(request, "gnfs_komenda/transfers/placement.html", {
        "transfer": transfer,
        "form": form,
    })


@login_required(login_url='login-staff')
def transfer_confirm_arrival_view(request, transfer_id):
    """Confirm arrival at destination command and activate new posting."""
    transfer = get_object_or_404(PersonnelTransfer, id=transfer_id)
    if request.method == 'POST':
        form = TransferConfirmArrivalForm(request.POST)
        if form.is_valid():
            try:
                confirm_arrival(
                    transfer,
                    request.user,
                    actual_reporting_date=form.cleaned_data['actual_reporting_date'],
                    comments=form.cleaned_data.get('remarks')
                )
                messages.success(request, f"Arrival confirmed! Officer {transfer.personnel.fullname} is now active at {transfer.destination_station}.")
                return redirect('transfer-detail', transfer_id=transfer.id)
            except ValidationError as e:
                form.add_error(None, str(e))
    else:
        form = TransferConfirmArrivalForm(initial={'actual_reporting_date': timezone.now().date()})

    return render(request, "gnfs_komenda/transfers/confirm.html", {
        "transfer": transfer,
        "form": form,
    })


@login_required(login_url='login-staff')
def transfer_cancel_view(request, transfer_id):
    """Cancel transfer before completion."""
    transfer = get_object_or_404(PersonnelTransfer, id=transfer_id)
    if request.method == 'POST':
        form = TransferCancelForm(request.POST)
        if form.is_valid():
            try:
                cancel_transfer(transfer, request.user, reason=form.cleaned_data['cancellation_reason'])
                messages.success(request, f"Transfer {transfer.reference_number} cancelled.")
                return redirect('transfer-detail', transfer_id=transfer.id)
            except ValidationError as e:
                form.add_error(None, str(e))
    else:
        form = TransferCancelForm()

    return render(request, "gnfs_komenda/transfers/cancel.html", {
        "transfer": transfer,
        "form": form,
    })


# ==============================================================================
# SECTION 17: REGIONAL & DISTRICT COMMAND LEADERSHIP VIEWS
# ==============================================================================

@login_required(login_url='login-staff')
def region_management_view(request, region_id):
    """
    Region Management Overview Page (Section 17.17).
    Displays current RFO & 2IC leadership, districts, stations, personnel,
    functional assignments, incoming/outgoing transfers, and personnel on leave.
    """
    region = get_object_or_404(Region, id=region_id)
    can_manage = can_manage_regional_leadership(request.user, region)

    districts = region.districts.filter(active=True).order_by('name')
    stations = Station.objects.filter(district__region=region, active=True).select_related('district').order_by('name')
    personnel_qs = NorminalRoll.active_objects.filter(station__district__region=region)

    personnel_count = personnel_qs.count()
    senior_officers = personnel_qs.filter(rank__officer_category=Rank.OfficerCategory.SENIOR).count()
    junior_officers = personnel_qs.filter(rank__officer_category=Rank.OfficerCategory.JUNIOR).count()
    personnel_on_leave = personnel_qs.filter(leave_status='ON_LEAVE').count()

    incoming_transfers = PersonnelTransfer.objects.filter(
        destination_region=region,
        status__in=[TransferStatus.PENDING_APPROVAL, TransferStatus.APPROVED, TransferStatus.RELEASED, TransferStatus.REPORTED]
    ).count()

    outgoing_transfers = PersonnelTransfer.objects.filter(
        source_region=region,
        status__in=[TransferStatus.PENDING_APPROVAL, TransferStatus.APPROVED, TransferStatus.RELEASED, TransferStatus.REPORTED]
    ).count()

    leadership_history = get_region_leadership_history(region)

    context = {
        "region": region,
        "current_rfo": region.current_rfo,
        "current_2ic": region.current_2ic,
        "can_manage": can_manage,
        "districts": districts,
        "stations": stations,
        "personnel_count": personnel_count,
        "senior_officers": senior_officers,
        "junior_officers": junior_officers,
        "personnel_on_leave": personnel_on_leave,
        "incoming_transfers": incoming_transfers,
        "outgoing_transfers": outgoing_transfers,
        "leadership_history": leadership_history,
    }
    return render(request, "gnfs_komenda/leadership/manage_region.html", context)


@login_required(login_url='login-staff')
def manage_regional_leadership_view(request, region_id):
    """
    Manage Regional Leadership Appointments (RFO & 2IC) (Section 17.18).
    Atomically closes prior appointments and creates auditable records.
    Never overwrites history.
    """
    region = get_object_or_404(Region, id=region_id)
    if not can_manage_regional_leadership(request.user, region):
        raise PermissionDenied("You do not have permission to manage leadership for this Region.")

    current_rfo = region.current_rfo
    current_2ic = region.current_2ic

    if request.method == 'POST':
        form = RegionLeadershipForm(request.POST, region=region)
        if form.is_valid():
            eff_date = form.cleaned_data['effective_date']
            ref = form.cleaned_data.get('appointment_reference', '').strip()
            remarks = form.cleaned_data.get('remarks', '').strip()

            new_rfo = form.cleaned_data.get('rfo')
            rfo_acting = form.cleaned_data.get('rfo_is_acting', False)

            new_2ic = form.cleaned_data.get('regional_2ic')
            reg_2ic_acting = form.cleaned_data.get('regional_2ic_is_acting', False)

            try:
                # Appoint RFO if selected and changed
                if new_rfo and (not current_rfo or current_rfo.personnel_id != new_rfo.id or current_rfo.is_acting != rfo_acting):
                    appoint_rfo(
                        region=region,
                        personnel=new_rfo,
                        effective_from=eff_date,
                        appointed_by=request.user,
                        appointment_reference=ref,
                        remarks=remarks,
                        is_acting=rfo_acting
                    )
                    messages.success(request, f"Regional Fire Officer appointment updated for {region.name}.")

                # Appoint 2IC if selected and changed
                if new_2ic and (not current_2ic or current_2ic.personnel_id != new_2ic.id or current_2ic.is_acting != reg_2ic_acting):
                    appoint_regional_2ic(
                        region=region,
                        personnel=new_2ic,
                        effective_from=eff_date,
                        appointed_by=request.user,
                        appointment_reference=ref,
                        remarks=remarks,
                        is_acting=reg_2ic_acting
                    )
                    messages.success(request, f"Regional Second-in-Command appointment updated for {region.name}.")

                return redirect('region-management', region_id=region.id)
            except ValidationError as e:
                form.add_error(None, str(e))
    else:
        initial = {
            'effective_date': timezone.now().date(),
        }
        if current_rfo:
            initial['rfo'] = current_rfo.personnel
            initial['rfo_is_acting'] = current_rfo.is_acting
            initial['appointment_reference'] = current_rfo.appointment_reference
        if current_2ic:
            initial['regional_2ic'] = current_2ic.personnel
            initial['regional_2ic_is_acting'] = current_2ic.is_acting

        form = RegionLeadershipForm(initial=initial, region=region)

    leadership_history = get_region_leadership_history(region)
    return render(request, "gnfs_komenda/leadership/manage_region.html", {
        "region": region,
        "form": form,
        "current_rfo": current_rfo,
        "current_2ic": current_2ic,
        "leadership_history": leadership_history,
        "editing": True,
    })


@login_required(login_url='login-staff')
def manage_district_leadership_view(request, district_id):
    """
    Manage District Command Leadership (DFO/MFO & 2IC) (Section 17.19, 17.20).
    Configurable command type (District, Municipal, Metropolitan) and designation.
    """
    district = get_object_or_404(District, id=district_id)
    if not can_manage_district_leadership(request.user, district):
        raise PermissionDenied("You do not have permission to manage leadership for this District Command.")

    current_co = district.current_commanding_officer
    current_2ic = district.current_2ic

    if request.method == 'POST':
        form = DistrictLeadershipForm(request.POST)
        if form.is_valid():
            # 1. Update District Configuration
            cmd_type = form.cleaned_data['command_type']
            co_desig = form.cleaned_data['co_designation']
            if district.command_type != cmd_type or district.co_designation != co_desig:
                district.command_type = cmd_type
                district.co_designation = co_desig
                district.save(update_fields=['command_type', 'co_designation'])

            eff_date = form.cleaned_data['effective_date']
            ref = form.cleaned_data.get('appointment_reference', '').strip()
            remarks = form.cleaned_data.get('remarks', '').strip()

            new_co = form.cleaned_data.get('commanding_officer')
            co_acting = form.cleaned_data.get('co_is_acting', False)

            new_2ic = form.cleaned_data.get('district_2ic')
            dist_2ic_acting = form.cleaned_data.get('district_2ic_is_acting', False)

            try:
                # Appoint Commanding Officer if selected and changed
                if new_co and (not current_co or current_co.personnel_id != new_co.id or current_co.is_acting != co_acting or current_co.appointment_type != co_desig):
                    appoint_commanding_officer(
                        district=district,
                        personnel=new_co,
                        appointment_type=co_desig,
                        effective_from=eff_date,
                        appointed_by=request.user,
                        appointment_reference=ref,
                        remarks=remarks,
                        is_acting=co_acting
                    )
                    messages.success(request, f"{co_desig} leadership appointment updated for {district.name}.")

                # Appoint 2IC if selected and changed
                if new_2ic and (not current_2ic or current_2ic.personnel_id != new_2ic.id or current_2ic.is_acting != dist_2ic_acting):
                    appoint_district_2ic(
                        district=district,
                        personnel=new_2ic,
                        effective_from=eff_date,
                        appointed_by=request.user,
                        appointment_reference=ref,
                        remarks=remarks,
                        is_acting=dist_2ic_acting
                    )
                    messages.success(request, f"District 2IC appointment updated for {district.name}.")

                return redirect('manage-district-leadership', district_id=district.id)
            except ValidationError as e:
                form.add_error(None, str(e))
    else:
        initial = {
            'command_type': district.command_type,
            'co_designation': district.co_designation or 'DFO',
            'effective_date': timezone.now().date(),
        }
        if current_co:
            initial['commanding_officer'] = current_co.personnel
            initial['co_is_acting'] = current_co.is_acting
            initial['appointment_reference'] = current_co.appointment_reference
        if current_2ic:
            initial['district_2ic'] = current_2ic.personnel
            initial['district_2ic_is_acting'] = current_2ic.is_acting

        form = DistrictLeadershipForm(initial=initial)

    leadership_history = get_district_leadership_history(district)
    stations = district.stations.filter(active=True).order_by('name')
    personnel_count = NorminalRoll.active_objects.filter(station__district=district).count()

    context = {
        "district": district,
        "form": form,
        "current_co": current_co,
        "current_2ic": current_2ic,
        "leadership_history": leadership_history,
        "stations": stations,
        "personnel_count": personnel_count,
    }
    return render(request, "gnfs_komenda/leadership/manage_district.html", context)


# ==============================================================================
# SECTION 17: FUNCTIONAL ORGANISATIONAL STRUCTURE CRUD & TREE VIEWS
# ==============================================================================

@login_required(login_url='login-staff')
def organisation_tree_view(request):
    """
    Interactive Hierarchical Tree View of GNFS Organisation (Section 17.30).
    Displays Directorates -> Departments -> Units with personnel counts and status.
    """
    tree = get_organisation_tree()
    can_manage = can_manage_functional_structure(request.user)

    total_directorates = FunctionalUnit.objects.filter(unit_type=FunctionalUnit.FunctionalUnitType.DIRECTORATE, active=True).count()
    total_departments = FunctionalUnit.objects.filter(unit_type__in=[FunctionalUnit.FunctionalUnitType.DEPARTMENT, FunctionalUnit.FunctionalUnitType.DIVISION], active=True).count()
    total_units = FunctionalUnit.objects.filter(unit_type=FunctionalUnit.FunctionalUnitType.UNIT, active=True).count()

    return render(request, "gnfs_komenda/organisation/tree.html", {
        "tree": tree,
        "can_manage": can_manage,
        "total_directorates": total_directorates,
        "total_departments": total_departments,
        "total_units": total_units,
    })


@login_required(login_url='login-staff')
def directorate_list_view(request):
    """List all Directorates with counts, active status and action buttons."""
    directorates = FunctionalUnit.objects.filter(
        unit_type=FunctionalUnit.FunctionalUnitType.DIRECTORATE
    ).order_by('display_order', 'name')
    can_manage = can_manage_functional_structure(request.user)
    return render(request, "gnfs_komenda/organisation/directorates_list.html", {
        "directorates": directorates,
        "can_manage": can_manage,
    })


@login_required(login_url='login-staff')
def directorate_create_view(request):
    """Create a new top-level Directorate."""
    if not can_manage_functional_structure(request.user):
        raise PermissionDenied("Only National Administrators can manage Directorates.")

    if request.method == 'POST':
        form = DirectorateForm(request.POST)
        if form.is_valid():
            try:
                create_functional_unit(
                    name=form.cleaned_data['name'],
                    unit_type=FunctionalUnit.FunctionalUnitType.DIRECTORATE,
                    parent=None,
                    code=form.cleaned_data.get('code', ''),
                    description=form.cleaned_data.get('description', ''),
                    display_order=form.cleaned_data.get('display_order', 0),
                    active=form.cleaned_data.get('active', True),
                    user=request.user
                )
                messages.success(request, f"Directorate '{form.cleaned_data['name']}' created successfully.")
                return redirect('directorates-list')
            except ValidationError as e:
                form.add_error(None, str(e))
    else:
        form = DirectorateForm()

    return render(request, "gnfs_komenda/organisation/unit_form.html", {
        "form": form,
        "title": "Add Directorate",
        "unit_type_label": "Directorate",
        "back_url": reverse('directorates-list'),
    })


@login_required(login_url='login-staff')
def directorate_update_view(request, pk):
    """Update an existing Directorate."""
    unit = get_object_or_404(FunctionalUnit, pk=pk, unit_type=FunctionalUnit.FunctionalUnitType.DIRECTORATE)
    if not can_manage_functional_structure(request.user):
        raise PermissionDenied("Only National Administrators can manage Directorates.")

    if request.method == 'POST':
        form = DirectorateForm(request.POST, instance=unit)
        if form.is_valid():
            try:
                update_functional_unit(
                    unit=unit,
                    name=form.cleaned_data['name'],
                    code=form.cleaned_data.get('code', ''),
                    description=form.cleaned_data.get('description', ''),
                    display_order=form.cleaned_data.get('display_order', 0),
                    active=form.cleaned_data.get('active', True),
                    user=request.user
                )
                messages.success(request, f"Directorate '{unit.name}' updated successfully.")
                return redirect('directorates-list')
            except ValidationError as e:
                form.add_error(None, str(e))
    else:
        form = DirectorateForm(instance=unit)

    return render(request, "gnfs_komenda/organisation/unit_form.html", {
        "form": form,
        "unit": unit,
        "title": f"Edit Directorate: {unit.name}",
        "unit_type_label": "Directorate",
        "back_url": reverse('directorates-list'),
    })


@login_required(login_url='login-staff')
def directorate_toggle_view(request, pk):
    """Safe soft-deactivation or reactivation of a Directorate."""
    unit = get_object_or_404(FunctionalUnit, pk=pk, unit_type=FunctionalUnit.FunctionalUnitType.DIRECTORATE)
    if not can_manage_functional_structure(request.user):
        raise PermissionDenied("Only National Administrators can manage Directorates.")

    if unit.active:
        deactivate_functional_unit(unit, user=request.user)
        messages.warning(request, f"Directorate '{unit.name}' and all sub-units have been safely deactivated.")
    else:
        try:
            reactivate_functional_unit(unit, user=request.user)
            messages.success(request, f"Directorate '{unit.name}' reactivated successfully.")
        except ValidationError as e:
            messages.error(request, str(e))

    return redirect('directorates-list')


@login_required(login_url='login-staff')
def department_list_view(request):
    """List all Departments/Divisions with parent Directorate and unit counts."""
    departments = FunctionalUnit.objects.filter(
        unit_type__in=[FunctionalUnit.FunctionalUnitType.DEPARTMENT, FunctionalUnit.FunctionalUnitType.DIVISION]
    ).select_related('parent').order_by('parent__name', 'display_order', 'name')
    can_manage = can_manage_functional_structure(request.user)
    return render(request, "gnfs_komenda/organisation/departments_list.html", {
        "departments": departments,
        "can_manage": can_manage,
    })


@login_required(login_url='login-staff')
def department_create_view(request):
    """Create a new Department under a selected Directorate."""
    if not can_manage_functional_structure(request.user):
        raise PermissionDenied("Only National Administrators can manage Departments.")

    if request.method == 'POST':
        form = DepartmentForm(request.POST)
        if form.is_valid():
            try:
                create_functional_unit(
                    name=form.cleaned_data['name'],
                    unit_type=FunctionalUnit.FunctionalUnitType.DEPARTMENT,
                    parent=form.cleaned_data['parent'],
                    code=form.cleaned_data.get('code', ''),
                    description=form.cleaned_data.get('description', ''),
                    display_order=form.cleaned_data.get('display_order', 0),
                    active=form.cleaned_data.get('active', True),
                    user=request.user
                )
                messages.success(request, f"Department '{form.cleaned_data['name']}' created successfully.")
                return redirect('departments-list')
            except ValidationError as e:
                form.add_error(None, str(e))
    else:
        form = DepartmentForm()

    return render(request, "gnfs_komenda/organisation/unit_form.html", {
        "form": form,
        "title": "Add Department / Division",
        "unit_type_label": "Department / Division",
        "back_url": reverse('departments-list'),
    })


@login_required(login_url='login-staff')
def department_update_view(request, pk):
    """Update an existing Department."""
    unit = get_object_or_404(
        FunctionalUnit,
        pk=pk,
        unit_type__in=[FunctionalUnit.FunctionalUnitType.DEPARTMENT, FunctionalUnit.FunctionalUnitType.DIVISION]
    )
    if not can_manage_functional_structure(request.user):
        raise PermissionDenied("Only National Administrators can manage Departments.")

    if request.method == 'POST':
        form = DepartmentForm(request.POST, instance=unit)
        if form.is_valid():
            try:
                update_functional_unit(
                    unit=unit,
                    name=form.cleaned_data['name'],
                    parent=form.cleaned_data['parent'],
                    code=form.cleaned_data.get('code', ''),
                    description=form.cleaned_data.get('description', ''),
                    display_order=form.cleaned_data.get('display_order', 0),
                    active=form.cleaned_data.get('active', True),
                    user=request.user
                )
                messages.success(request, f"Department '{unit.name}' updated successfully.")
                return redirect('departments-list')
            except ValidationError as e:
                form.add_error(None, str(e))
    else:
        form = DepartmentForm(instance=unit)

    return render(request, "gnfs_komenda/organisation/unit_form.html", {
        "form": form,
        "unit": unit,
        "title": f"Edit Department: {unit.name}",
        "unit_type_label": "Department / Division",
        "back_url": reverse('departments-list'),
    })


@login_required(login_url='login-staff')
def department_toggle_view(request, pk):
    """Safe soft-deactivation or reactivation of a Department."""
    unit = get_object_or_404(
        FunctionalUnit,
        pk=pk,
        unit_type__in=[FunctionalUnit.FunctionalUnitType.DEPARTMENT, FunctionalUnit.FunctionalUnitType.DIVISION]
    )
    if not can_manage_functional_structure(request.user):
        raise PermissionDenied("Only National Administrators can manage Departments.")

    if unit.active:
        deactivate_functional_unit(unit, user=request.user)
        messages.warning(request, f"Department '{unit.name}' and its sub-units have been safely deactivated.")
    else:
        try:
            reactivate_functional_unit(unit, user=request.user)
            messages.success(request, f"Department '{unit.name}' reactivated successfully.")
        except ValidationError as e:
            messages.error(request, str(e))

    return redirect('departments-list')


@login_required(login_url='login-staff')
def unit_list_view(request):
    """List all functional Units with parent Department and Directorate."""
    units = FunctionalUnit.objects.filter(
        unit_type=FunctionalUnit.FunctionalUnitType.UNIT
    ).select_related('parent', 'parent__parent').order_by('parent__parent__name', 'parent__name', 'display_order', 'name')
    can_manage = can_manage_functional_structure(request.user)
    return render(request, "gnfs_komenda/organisation/units_list.html", {
        "units": units,
        "can_manage": can_manage,
    })


@login_required(login_url='login-staff')
def unit_create_view(request):
    """Create a new functional Unit under a selected Department."""
    if not can_manage_functional_structure(request.user):
        raise PermissionDenied("Only National Administrators can manage Units.")

    if request.method == 'POST':
        form = UnitForm(request.POST)
        if form.is_valid():
            try:
                create_functional_unit(
                    name=form.cleaned_data['name'],
                    unit_type=FunctionalUnit.FunctionalUnitType.UNIT,
                    parent=form.cleaned_data['parent'],
                    code=form.cleaned_data.get('code', ''),
                    description=form.cleaned_data.get('description', ''),
                    display_order=form.cleaned_data.get('display_order', 0),
                    active=form.cleaned_data.get('active', True),
                    user=request.user
                )
                messages.success(request, f"Unit '{form.cleaned_data['name']}' created successfully.")
                return redirect('units-list')
            except ValidationError as e:
                form.add_error(None, str(e))
    else:
        form = UnitForm()

    return render(request, "gnfs_komenda/organisation/unit_form.html", {
        "form": form,
        "title": "Add Unit",
        "unit_type_label": "Unit",
        "back_url": reverse('units-list'),
    })


@login_required(login_url='login-staff')
def unit_update_view(request, pk):
    """Update an existing Unit."""
    unit = get_object_or_404(FunctionalUnit, pk=pk, unit_type=FunctionalUnit.FunctionalUnitType.UNIT)
    if not can_manage_functional_structure(request.user):
        raise PermissionDenied("Only National Administrators can manage Units.")

    if request.method == 'POST':
        form = UnitForm(request.POST, instance=unit)
        if form.is_valid():
            try:
                update_functional_unit(
                    unit=unit,
                    name=form.cleaned_data['name'],
                    parent=form.cleaned_data['parent'],
                    code=form.cleaned_data.get('code', ''),
                    description=form.cleaned_data.get('description', ''),
                    display_order=form.cleaned_data.get('display_order', 0),
                    active=form.cleaned_data.get('active', True),
                    user=request.user
                )
                messages.success(request, f"Unit '{unit.name}' updated successfully.")
                return redirect('units-list')
            except ValidationError as e:
                form.add_error(None, str(e))
    else:
        form = UnitForm(instance=unit)

    return render(request, "gnfs_komenda/organisation/unit_form.html", {
        "form": form,
        "unit": unit,
        "title": f"Edit Unit: {unit.name}",
        "unit_type_label": "Unit",
        "back_url": reverse('units-list'),
    })


@login_required(login_url='login-staff')
def unit_toggle_view(request, pk):
    """Safe soft-deactivation or reactivation of a Unit."""
    unit = get_object_or_404(FunctionalUnit, pk=pk, unit_type=FunctionalUnit.FunctionalUnitType.UNIT)
    if not can_manage_functional_structure(request.user):
        raise PermissionDenied("Only National Administrators can manage Units.")

    if unit.active:
        deactivate_functional_unit(unit, user=request.user)
        messages.warning(request, f"Unit '{unit.name}' has been safely deactivated.")
    else:
        try:
            reactivate_functional_unit(unit, user=request.user)
            messages.success(request, f"Unit '{unit.name}' reactivated successfully.")
        except ValidationError as e:
            messages.error(request, str(e))

    return redirect('units-list')


# ==============================================================================
# SECTION 17: MULTIPLE FUNCTIONAL ASSIGNMENTS ON PERSONNEL
# ==============================================================================

@login_required(login_url='login-staff')
def personnel_add_functional_assignment_view(request, personnel_id):
    """Add a functional assignment to an officer (Section 17.11 - 17.15)."""
    personnel = get_object_or_404(NorminalRoll, id=personnel_id)
    if not can_user_manage_personnel(request.user, personnel):
        raise PermissionDenied("You do not have permission to manage this officer's assignments.")

    if request.method == 'POST':
        form = PersonnelFunctionalAssignmentCreateForm(request.POST)
        if form.is_valid():
            try:
                assign_functional_unit(
                    personnel=personnel,
                    functional_unit=form.cleaned_data['functional_unit'],
                    assignment_role=form.cleaned_data.get('assignment_role', 'Member'),
                    is_primary=form.cleaned_data.get('is_primary', False),
                    start_date=form.cleaned_data['start_date'],
                    assigned_by=request.user,
                    reason=form.cleaned_data.get('reason', '')
                )
                messages.success(request, f"Functional assignment added for {personnel.fullname}.")
                return redirect('staff-detail', pk=personnel.id)
            except ValidationError as e:
                form.add_error(None, str(e))
    else:
        form = PersonnelFunctionalAssignmentCreateForm(initial={'start_date': timezone.now().date()})

    return render(request, "gnfs_komenda/organisation/assignment_form.html", {
        "personnel": personnel,
        "form": form,
        "title": f"Add Functional Assignment - {personnel.fullname}",
    })


@login_required(login_url='login-staff')
@require_POST
def personnel_remove_functional_assignment_view(request, assignment_id):
    """Safely remove a functional assignment, preserving history (Section 17.11)."""
    assignment = get_object_or_404(PersonnelFunctionalAssignment, id=assignment_id)
    personnel = assignment.personnel
    if not can_user_manage_personnel(request.user, personnel):
        raise PermissionDenied("You do not have permission to manage this officer's assignments.")

    reason = request.POST.get('reason', 'Removed by administrator.')
    remove_functional_assignment(assignment, user=request.user, reason=reason)
    messages.warning(request, f"Functional assignment '{assignment.functional_unit.name}' removed for {personnel.fullname}.")
    return redirect('staff-detail', pk=personnel.id)


@login_required(login_url='login-staff')
@require_POST
def personnel_set_primary_assignment_view(request, assignment_id):
    """Set a specific active assignment as primary (Section 17.13)."""
    assignment = get_object_or_404(PersonnelFunctionalAssignment, id=assignment_id)
    personnel = assignment.personnel
    if not can_user_manage_personnel(request.user, personnel):
        raise PermissionDenied("You do not have permission to manage this officer's assignments.")

    set_primary_functional_assignment(personnel, assignment, user=request.user)
    messages.success(request, f"'{assignment.functional_unit.name}' is now designated as Primary assignment for {personnel.fullname}.")
    return redirect('staff-detail', pk=personnel.id)