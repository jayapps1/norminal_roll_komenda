"""
Workflow Inboxes & Administrative Processing Views for GNFS Leave Management.
Provides dedicated operational and executive decision portals:
1. District Admin Processing Inbox & Detail Review (/district/<id>/leave/inbox/)
2. DFO / MFO Executive Approval Inbox & Decision (/district/<id>/leave/approvals/)
3. Regional Admin Processing Inbox & Detail Review (/regional/<id>/leave/inbox/)
4. RFO Executive Approval Inbox & Decision (/regional/<id>/leave/approvals/)

Enforces:
- Separation of administrative processing (verification) vs. command approval.
- Universal Anti-Self-Approval across all command levels.
- Dynamic routing resolution for DFO/MFO personal leave to RFO.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q, Count
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone

from gnfs_komenda.models import (
    LeaveRequest, LeaveApprovalDecision, District, Region, NorminalRoll,
    CommandAppointment, LeaveEntitlement
)
from gnfs_komenda.security import (
    get_user_role, get_user_assignment, get_personnel_for_user
)
from gnfs_komenda.services.leave_routing_service import (
    can_user_process_leave, can_user_final_approve_leave,
    process_leave_request, final_approve_leave_request,
    final_reject_leave_request
)
from gnfs_komenda.services.permission_resolver import (
    can_manage_district_leadership, can_manage_regional_leadership
)


# ==============================================================================
# DISTRICT WORKFLOW INBOXES (DISTRICT ADMIN & DFO/MFO)
# ==============================================================================

@login_required(login_url='login-staff')
def district_leave_inbox_view(request, district_id):
    """
    District Administration Leave Processing Inbox.
    Where District Admin reviews, verifies documents, and forwards to DFO/MFO.
    """
    district = get_object_or_404(District, id=district_id, active=True)
    user_role = get_user_role(request.user)
    assignment = get_user_assignment(request.user)

    # Permission check
    is_district_admin = (
        request.user.is_superuser or user_role == "NATIONAL_ADMIN" or
        (user_role in ("DISTRICT_ADMIN", "DFO") and assignment and assignment.district_id == district.id) or
        (user_role == "REGIONAL_ADMIN" and assignment and assignment.region_id == district.region_id)
    )
    if not is_district_admin:
        raise PermissionDenied("You are not authorized to access District Administration for this command.")

    all_district_leaves = LeaveRequest.objects.filter(
        routing_district=district
    ).select_related('personnel', 'leave_type', 'entitlement', 'requested_by')

    pending_processing = all_district_leaves.filter(status=LeaveRequest.Status.PENDING_PROCESSING).order_by('-created_at')
    forwarded = all_district_leaves.filter(status=LeaveRequest.Status.PENDING_APPROVAL).order_by('-processed_at')
    returned = all_district_leaves.filter(status=LeaveRequest.Status.RETURNED_FOR_CORRECTION).order_by('-updated_at')
    approved = all_district_leaves.filter(status__in=[LeaveRequest.Status.APPROVED, LeaveRequest.Status.ACTIVE, LeaveRequest.Status.COMPLETED]).order_by('-approved_at')[:20]
    rejected = all_district_leaves.filter(status=LeaveRequest.Status.REJECTED).order_by('-updated_at')[:15]

    currently_on_leave = NorminalRoll.active_objects.filter(
        station__district=district,
        leave_status="ON LEAVE"
    ).select_related('station')

    return render(request, "gnfs_komenda/inboxes/district_admin_inbox.html", {
        "district": district,
        "pending_processing": pending_processing,
        "forwarded": forwarded,
        "returned": returned,
        "approved": approved,
        "rejected": rejected,
        "currently_on_leave": currently_on_leave,
        "co": district.current_commanding_officer,
        "co_desig": district.get_co_designation_display() if hasattr(district, 'get_co_designation_display') else "DFO",
    })


@login_required(login_url='login-staff')
def district_leave_process_view(request, district_id, pk):
    """
    District Admin Detail Verification & Action View.
    Allows District Admin to view documents, dates, balances, and either
    FORWARD to DFO/MFO or RETURN FOR CORRECTION.
    """
    district = get_object_or_404(District, id=district_id, active=True)
    leave_req = get_object_or_404(
        LeaveRequest.objects.select_related(
            'personnel', 'leave_type', 'entitlement', 'requested_by', 'routing_district', 'routing_region'
        ),
        pk=pk
    )

    if not can_user_process_leave(request.user, leave_req):
        raise PermissionDenied("You are not authorized to process this leave request.")

    if request.method == 'POST':
        action = request.POST.get('action') # 'FORWARD' or 'RETURN_FOR_CORRECTION'
        comments = request.POST.get('comments', '').strip()
        try:
            process_leave_request(pk, request.user, action=action, comments=comments)
            if action == 'FORWARD':
                messages.success(request, f"Leave request for {leave_req.personnel.fullname} verified and forwarded to DFO/MFO.")
            else:
                messages.warning(request, f"Leave request returned to {leave_req.personnel.fullname} for correction.")
            return redirect('district-leave-inbox', district_id=district.id)
        except (ValidationError, Exception) as e:
            messages.error(request, str(e))

    decisions = leave_req.decisions.all().select_related('approver').order_by('timestamp')
    entitlement = leave_req.entitlement

    return render(request, "gnfs_komenda/inboxes/district_process_detail.html", {
        "district": district,
        "leave_req": leave_req,
        "decisions": decisions,
        "entitlement": entitlement,
    })


@login_required(login_url='login-staff')
def district_leave_approvals_view(request, district_id):
    """
    DFO / MFO Executive Approval Inbox.
    Shows leave requests forwarded by District Admin that require Command Decision.
    """
    district = get_object_or_404(District, id=district_id, active=True)
    user_personnel = get_personnel_for_user(request.user)

    # Authorized: Current active DFO/MFO or Superuser / National Admin
    is_authorized_co = (
        request.user.is_superuser or get_user_role(request.user) == "NATIONAL_ADMIN" or
        (district.current_commanding_officer and user_personnel and district.current_commanding_officer.personnel_id == user_personnel.id)
    )
    if not is_authorized_co:
        # Fallback check for district admin role
        assignment = get_user_assignment(request.user)
        if not (assignment and assignment.active and assignment.role in ("DFO", "DISTRICT_ADMIN") and assignment.district_id == district.id):
            raise PermissionDenied("You are not authorized to access the Commanding Officer Approval Inbox for this district.")

    # Requests awaiting DFO/MFO command approval
    awaiting_approval = LeaveRequest.objects.filter(
        routing_district=district,
        status=LeaveRequest.Status.PENDING_APPROVAL
    ).select_related('personnel', 'leave_type', 'entitlement', 'processed_by', 'personnel__station').order_by('-processed_at')

    # History of decisions made
    recent_approved = LeaveRequest.objects.filter(
        routing_district=district,
        status__in=[LeaveRequest.Status.APPROVED, LeaveRequest.Status.ACTIVE]
    ).select_related('personnel', 'leave_type', 'approved_by').order_by('-approved_at')[:15]

    recent_rejected = LeaveRequest.objects.filter(
        routing_district=district,
        status=LeaveRequest.Status.REJECTED
    ).select_related('personnel', 'leave_type', 'approved_by').order_by('-updated_at')[:10]

    return render(request, "gnfs_komenda/inboxes/dfo_approval_inbox.html", {
        "district": district,
        "awaiting_approval": awaiting_approval,
        "recent_approved": recent_approved,
        "recent_rejected": recent_rejected,
        "co_desig": district.get_co_designation_display() if hasattr(district, 'get_co_designation_display') else "DFO",
        "current_co": district.current_commanding_officer,
    })


@login_required(login_url='login-staff')
def district_leave_decision_view(request, district_id, pk):
    """
    DFO / MFO Command Decision View (Approve, Reject, or Return for Correction).
    Strictly enforces Anti-Self-Approval and revalidation.
    """
    district = get_object_or_404(District, id=district_id, active=True)
    leave_req = get_object_or_404(
        LeaveRequest.objects.select_related('personnel', 'leave_type', 'entitlement', 'processed_by'),
        pk=pk
    )

    if request.method == 'POST':
        action = request.POST.get('action') # 'APPROVE', 'REJECT', 'RETURN_FOR_CORRECTION'
        comments = request.POST.get('comments', '').strip()

        try:
            if action == 'APPROVE':
                final_approve_leave_request(pk, request.user, comments=comments)
                messages.success(request, f"Leave application for {leave_req.personnel.fullname} has been approved.")
            elif action == 'REJECT':
                if not comments:
                    raise ValidationError("A reason must be provided when rejecting a leave request.")
                final_reject_leave_request(pk, request.user, comments=comments)
                messages.warning(request, f"Leave application for {leave_req.personnel.fullname} was rejected.")
            elif action == 'RETURN_FOR_CORRECTION':
                if not comments:
                    raise ValidationError("Please provide instructions when returning a request for correction.")
                process_leave_request(pk, request.user, action='RETURN_FOR_CORRECTION', comments=comments)
                messages.info(request, f"Leave request returned to {leave_req.personnel.fullname} for correction.")
            return redirect('district-leave-approvals', district_id=district.id)

        except (PermissionDenied, ValidationError, Exception) as e:
            messages.error(request, str(e))

    decisions = leave_req.decisions.all().select_related('approver').order_by('timestamp')
    can_approve = can_user_final_approve_leave(request.user, leave_req)

    return render(request, "gnfs_komenda/inboxes/dfo_decision_detail.html", {
        "district": district,
        "leave_req": leave_req,
        "decisions": decisions,
        "can_approve": can_approve,
        "entitlement": leave_req.entitlement,
    })


# ==============================================================================
# REGIONAL WORKFLOW INBOXES (REGIONAL ADMIN & RFO)
# ==============================================================================

@login_required(login_url='login-staff')
def regional_leave_inbox_view(request, region_id):
    """
    Regional Administration Leave Processing Inbox.
    Reviews requests for Regional personnel and DFO/MFO personal leave.
    """
    region = get_object_or_404(Region, id=region_id, active=True)
    user_role = get_user_role(request.user)
    assignment = get_user_assignment(request.user)

    is_regional_admin = (
        request.user.is_superuser or user_role == "NATIONAL_ADMIN" or
        (user_role == "REGIONAL_ADMIN" and assignment and assignment.region_id == region.id)
    )
    if not is_regional_admin:
        raise PermissionDenied("You are not authorized to access Regional Administration for this region.")

    # All requests routed to this region at the administrative stage
    regional_requests = LeaveRequest.objects.filter(
        routing_region=region,
        processing_stage=LeaveRequest.ProcessingStage.REGIONAL_ADMIN
    ).select_related('personnel', 'leave_type', 'entitlement', 'requested_by', 'routing_district')

    # Divide between DFO/MFO personal leave requests and direct regional staff
    co_appt_types = ('DFO', 'MFO', 'METRO_FO', 'ACTING_DFO', 'ACTING_MFO', 'ACTING_METRO_FO')
    co_officer_ids = CommandAppointment.objects.filter(
        region=region,
        appointment_type__in=co_appt_types,
        is_active=True
    ).values_list('personnel_id', flat=True)

    pending_co_leaves = regional_requests.filter(
        status=LeaveRequest.Status.PENDING_PROCESSING,
        personnel_id__in=co_officer_ids
    ).order_by('-created_at')

    pending_regional_staff = regional_requests.filter(
        status=LeaveRequest.Status.PENDING_PROCESSING
    ).exclude(personnel_id__in=co_officer_ids).order_by('-created_at')

    forwarded_to_rfo = LeaveRequest.objects.filter(
        routing_region=region,
        status=LeaveRequest.Status.PENDING_APPROVAL
    ).select_related('personnel', 'leave_type', 'processed_by').order_by('-processed_at')

    returned_requests = LeaveRequest.objects.filter(
        routing_region=region,
        status=LeaveRequest.Status.RETURNED_FOR_CORRECTION
    ).select_related('personnel', 'leave_type').order_by('-updated_at')

    return render(request, "gnfs_komenda/inboxes/regional_admin_inbox.html", {
        "region": region,
        "pending_co_leaves": pending_co_leaves,
        "pending_regional_staff": pending_regional_staff,
        "forwarded_to_rfo": forwarded_to_rfo,
        "returned_requests": returned_requests,
        "current_rfo": region.current_rfo,
        "current_2ic": region.current_2ic,
    })


@login_required(login_url='login-staff')
def regional_leave_process_view(request, region_id, pk):
    """
    Regional Admin Verification & Action View.
    Allows Regional Admin to view details, verify documents, and FORWARD to RFO
    or RETURN FOR CORRECTION.
    """
    region = get_object_or_404(Region, id=region_id, active=True)
    leave_req = get_object_or_404(
        LeaveRequest.objects.select_related(
            'personnel', 'leave_type', 'entitlement', 'requested_by', 'routing_district', 'routing_region'
        ),
        pk=pk
    )

    if not can_user_process_leave(request.user, leave_req):
        raise PermissionDenied("You are not authorized to process this leave request.")

    if request.method == 'POST':
        action = request.POST.get('action') # 'FORWARD' or 'RETURN_FOR_CORRECTION'
        comments = request.POST.get('comments', '').strip()
        try:
            process_leave_request(pk, request.user, action=action, comments=comments)
            if action == 'FORWARD':
                messages.success(request, f"Leave request for {leave_req.personnel.fullname} verified and forwarded to RFO for decision.")
            else:
                messages.warning(request, f"Leave request returned to {leave_req.personnel.fullname} for correction.")
            return redirect('regional-leave-inbox', region_id=region.id)
        except (ValidationError, Exception) as e:
            messages.error(request, str(e))

    decisions = leave_req.decisions.all().select_related('approver').order_by('timestamp')

    return render(request, "gnfs_komenda/inboxes/regional_process_detail.html", {
        "region": region,
        "leave_req": leave_req,
        "decisions": decisions,
        "entitlement": leave_req.entitlement,
    })


@login_required(login_url='login-staff')
def regional_leave_approvals_view(request, region_id):
    """
    Regional Fire Officer (RFO) Executive Approval Inbox.
    Shows leave requests forwarded by Regional Admin, clearly distinguishing:
    1. DFO / MFO Personal Leave Requests
    2. Regional Personnel Leave Requests
    """
    region = get_object_or_404(Region, id=region_id, active=True)
    user_personnel = get_personnel_for_user(request.user)

    is_authorized_rfo = (
        request.user.is_superuser or get_user_role(request.user) == "NATIONAL_ADMIN" or
        (region.current_rfo and user_personnel and region.current_rfo.personnel_id == user_personnel.id)
    )
    if not is_authorized_rfo:
        assignment = get_user_assignment(request.user)
        if not (assignment and assignment.active and assignment.role == "REGIONAL_ADMIN" and assignment.region_id == region.id):
            raise PermissionDenied("You are not authorized to access the RFO Approval Inbox for this region.")

    all_awaiting = LeaveRequest.objects.filter(
        routing_region=region,
        status=LeaveRequest.Status.PENDING_APPROVAL
    ).select_related('personnel', 'leave_type', 'entitlement', 'processed_by', 'routing_district').order_by('-processed_at')

    # Identify DFO/MFO applicants
    co_appt_types = ('DFO', 'MFO', 'METRO_FO', 'ACTING_DFO', 'ACTING_MFO', 'ACTING_METRO_FO')
    co_officer_ids = CommandAppointment.objects.filter(
        region=region,
        appointment_type__in=co_appt_types,
        is_active=True
    ).values_list('personnel_id', flat=True)

    dfo_requests = all_awaiting.filter(personnel_id__in=co_officer_ids)
    regional_staff_requests = all_awaiting.exclude(personnel_id__in=co_officer_ids)

    # History
    recent_approved = LeaveRequest.objects.filter(
        routing_region=region,
        status__in=[LeaveRequest.Status.APPROVED, LeaveRequest.Status.ACTIVE]
    ).select_related('personnel', 'leave_type', 'approved_by').order_by('-approved_at')[:15]

    return render(request, "gnfs_komenda/inboxes/rfo_approval_inbox.html", {
        "region": region,
        "dfo_requests": dfo_requests,
        "regional_staff_requests": regional_staff_requests,
        "recent_approved": recent_approved,
        "current_rfo": region.current_rfo,
    })


@login_required(login_url='login-staff')
def regional_leave_decision_view(request, region_id, pk):
    """
    RFO Executive Decision View (Approve, Reject, or Return for Correction).
    Strictly revalidates all rules and prevents self-approval.
    """
    region = get_object_or_404(Region, id=region_id, active=True)
    leave_req = get_object_or_404(
        LeaveRequest.objects.select_related('personnel', 'leave_type', 'entitlement', 'processed_by'),
        pk=pk
    )

    if request.method == 'POST':
        action = request.POST.get('action') # 'APPROVE', 'REJECT', 'RETURN_FOR_CORRECTION'
        comments = request.POST.get('comments', '').strip()

        try:
            if action == 'APPROVE':
                final_approve_leave_request(pk, request.user, comments=comments)
                messages.success(request, f"Leave application for {leave_req.personnel.fullname} approved by RFO.")
            elif action == 'REJECT':
                if not comments:
                    raise ValidationError("A justification is required when rejecting a leave request.")
                final_reject_leave_request(pk, request.user, comments=comments)
                messages.warning(request, f"Leave application for {leave_req.personnel.fullname} was rejected.")
            elif action == 'RETURN_FOR_CORRECTION':
                if not comments:
                    raise ValidationError("Please provide instructions when returning a request for correction.")
                process_leave_request(pk, request.user, action='RETURN_FOR_CORRECTION', comments=comments)
                messages.info(request, f"Leave request returned to {leave_req.personnel.fullname} for correction.")
            return redirect('regional-leave-approvals', region_id=region.id)

        except (PermissionDenied, ValidationError, Exception) as e:
            messages.error(request, str(e))

    decisions = leave_req.decisions.all().select_related('approver').order_by('timestamp')
    can_approve = can_user_final_approve_leave(request.user, leave_req)

    return render(request, "gnfs_komenda/inboxes/rfo_decision_detail.html", {
        "region": region,
        "leave_req": leave_req,
        "decisions": decisions,
        "can_approve": can_approve,
        "entitlement": leave_req.entitlement,
    })
