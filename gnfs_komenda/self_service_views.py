"""
Personnel Self-Service Views for GNFS Personnel Management System.
Implements personal self-service portal:
- My Dashboard (/my/dashboard/)
- My Leave (/my/leave/)
- Request Leave (/my/leave/request/)
- Leave Request Detail (/my/leave/<pk>/)
- Correct & Resubmit Leave (/my/leave/<pk>/edit/)
- My Profile (/my/profile/)
- My Posting (/my/posting/)
- My Departments / Functional Assignments (/my/departments/)
- My Transfers (/my/transfers/)
- My Notifications (/my/notifications/)
"""

from datetime import datetime
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse, HttpResponseRedirect
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone

from gnfs_komenda.models import (
    NorminalRoll, LeaveRequest, LeaveEntitlement, LeaveType,
    LeaveApprovalDecision, PersonnelPosting, PersonnelFunctionalAssignment,
    PersonnelTransfer, TransferStatus, Notification, Rank
)
from gnfs_komenda.security import get_personnel_for_user
from gnfs_komenda.services.leave_routing_service import (
    resolve_leave_route, submit_personnel_leave_request,
    resubmit_corrected_leave_request
)
from gnfs_komenda.services.leave_calculator import calculate_leave_dates


@login_required(login_url='login-staff')
def my_dashboard_view(request):
    """
    Central Personnel Self-Service Dashboard.
    Available to EVERY user linked to a GNFS personnel record.
    """
    personnel = get_personnel_for_user(request.user)
    if not personnel:
        # User not yet linked to a personnel record
        return render(request, "gnfs_komenda/self_service/unlinked.html", {
            "user": request.user,
        })

    today = timezone.localdate()
    annual_type = LeaveType.objects.filter(code="ANNUAL").first()

    entitlement = None
    if annual_type:
        entitlement, _ = LeaveEntitlement.objects.get_or_create(
            personnel=personnel,
            leave_year=today.year,
            leave_type=annual_type,
            defaults={
                'base_entitlement': annual_type.get_entitlement_for_category(personnel.officer_category)
            }
        )

    # Next approved leave
    next_approved = LeaveRequest.objects.filter(
        personnel=personnel,
        status__in=[LeaveRequest.Status.APPROVED, LeaveRequest.Status.ACTIVE],
        last_leave_date__gte=today
    ).order_by('start_date').first()

    # Pending requests count
    pending_count = LeaveRequest.objects.filter(
        personnel=personnel,
        status__in=[
            LeaveRequest.Status.PENDING_PROCESSING,
            LeaveRequest.Status.PENDING_APPROVAL,
            LeaveRequest.Status.RETURNED_FOR_CORRECTION,
        ]
    ).count()

    # Active transfer
    active_transfer = PersonnelTransfer.objects.filter(
        personnel=personnel,
        status__in=[
            TransferStatus.DRAFT,
            TransferStatus.PENDING_APPROVAL,
            TransferStatus.APPROVED,
            TransferStatus.RELEASED,
            TransferStatus.REPORTED,
        ]
    ).order_by('-id').first()

    # Unread notifications
    unread_notifs = Notification.objects.filter(recipient=request.user, is_read=False).count()
    recent_notifs = Notification.objects.filter(recipient=request.user).order_by('-created_at')[:5]

    # Recent leave requests
    recent_leaves = LeaveRequest.objects.filter(personnel=personnel).select_related('leave_type').order_by('-created_at')[:5]

    # Command appointment & functional assignments
    command_appointment = personnel.current_command_appointment
    primary_assignment = personnel.primary_functional_assignment
    active_assignments = personnel.active_functional_assignments

    return render(request, "gnfs_komenda/self_service/dashboard.html", {
        "personnel": personnel,
        "entitlement": entitlement,
        "annual_type": annual_type,
        "next_approved": next_approved,
        "pending_count": pending_count,
        "active_transfer": active_transfer,
        "unread_notifs": unread_notifs,
        "recent_notifs": recent_notifs,
        "recent_leaves": recent_leaves,
        "command_appointment": command_appointment,
        "primary_assignment": primary_assignment,
        "active_assignments": active_assignments,
        "today": today,
    })


@login_required(login_url='login-staff')
def my_leave_view(request):
    """
    My Leave Management Hub showing entitlements, balances, and personal leave history.
    """
    personnel = get_personnel_for_user(request.user)
    if not personnel:
        return redirect('my-dashboard')

    today = timezone.localdate()
    year = int(request.GET.get('year', today.year))

    # All entitlements for the requested year
    entitlements = LeaveEntitlement.objects.filter(
        personnel=personnel,
        leave_year=year
    ).select_related('leave_type')

    # Leave history for this officer
    leave_requests_qs = LeaveRequest.objects.filter(
        personnel=personnel
    ).select_related('leave_type', 'approved_by', 'processed_by').order_by('-start_date', '-created_at')

    status_filter = request.GET.get('status', 'all')
    if status_filter != 'all' and status_filter in LeaveRequest.Status.values:
        leave_requests_qs = leave_requests_qs.filter(status=status_filter)

    if request.GET.get('year_filter'):
        try:
            yf = int(request.GET.get('year_filter'))
            leave_requests_qs = leave_requests_qs.filter(leave_year=yf)
        except ValueError:
            pass

    paginator = Paginator(leave_requests_qs, 15)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    annual_type = LeaveType.objects.filter(code="ANNUAL").first()
    annual_ent = None
    if annual_type:
        annual_ent = entitlements.filter(leave_type=annual_type).first()
        if not annual_ent:
            annual_ent, _ = LeaveEntitlement.objects.get_or_create(
                personnel=personnel,
                leave_year=year,
                leave_type=annual_type,
                defaults={'base_entitlement': annual_type.get_entitlement_for_category(personnel.officer_category)}
            )

    return render(request, "gnfs_komenda/self_service/my_leave.html", {
        "personnel": personnel,
        "year": year,
        "entitlements": entitlements,
        "annual_ent": annual_ent,
        "page_obj": page_obj,
        "status_filter": status_filter,
    })


@login_required(login_url='login-staff')
def my_leave_request_view(request):
    """
    Self-Service Leave Application Form.
    Officer details are automatically pre-populated from the user's personnel record.
    """
    personnel = get_personnel_for_user(request.user)
    if not personnel:
        messages.error(request, "Your account is not linked to a personnel record.")
        return redirect('my-dashboard')

    today = timezone.localdate()
    from gnfs_komenda.services.leave_policy_service import (
        get_eligible_leave_types_for_personnel,
        get_base_annual_entitlement
    )

    leave_types = get_eligible_leave_types_for_personnel(personnel)
    sex_not_set = getattr(personnel, 'gender', None) in (None, "", "NOT_SET")
    base_annual_days = get_base_annual_entitlement(personnel.officer_category)

    if request.method == 'POST':
        leave_type_id = request.POST.get('leave_type')
        start_date_str = request.POST.get('start_date')
        duration_str = request.POST.get('duration')
        reason = request.POST.get('reason', '').strip()
        contact_phone = request.POST.get('contact_phone', '').strip()
        contact_address = request.POST.get('contact_address', '').strip()
        handover_notes = request.POST.get('handover_notes', '').strip()
        med_ref = request.POST.get('medical_reference', '').strip()
        is_prop = request.POST.get('is_proportionate') in ('on', 'true', '1')
        attachment = request.FILES.get('attachment')

        try:
            if not leave_type_id:
                raise ValidationError("Please select a Leave Type.")
            if not start_date_str:
                raise ValidationError("Please enter a Start Date.")
            if not duration_str:
                raise ValidationError("Please specify the number of days.")

            leave_type = get_object_or_404(LeaveType, pk=leave_type_id)
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            duration = int(duration_str)

            # Sick leave requires hospital / medical reference or attachment
            if leave_type.code == "SICK" and not med_ref and not attachment:
                raise ValidationError("Sick leave applications require a medical reference or excuse-duty document attachment.")

            leave_req = submit_personnel_leave_request(
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
                is_proportionate=is_prop
            )

            messages.success(request, f"Your {leave_type.name} application for {duration} days has been submitted successfully.")
            return redirect('my-leave-detail', pk=leave_req.pk)

        except ValidationError as e:
            messages.error(request, str(e.message if hasattr(e, 'message') else e))
        except Exception as e:
            messages.error(request, f"Error submitting request: {str(e)}")

    # Route preview
    route_info = resolve_leave_route(personnel)

    # Entitlement info
    annual_type = LeaveType.objects.filter(code="ANNUAL").first()
    annual_balance = 0
    if annual_type:
        ent, _ = LeaveEntitlement.objects.get_or_create(
            personnel=personnel,
            leave_year=today.year,
            leave_type=annual_type,
            defaults={'base_entitlement': base_annual_days}
        )
        annual_balance = ent.remaining_balance

    return render(request, "gnfs_komenda/self_service/request_leave.html", {
        "personnel": personnel,
        "leave_types": leave_types,
        "route_info": route_info,
        "annual_balance": annual_balance,
        "base_annual_days": base_annual_days,
        "sex_not_set": sex_not_set,
        "today": today,
    })


@login_required(login_url='login-staff')
def my_leave_detail_view(request, pk):
    """
    Self-Service Leave Request Detail with audit timeline and progress status.
    """
    personnel = get_personnel_for_user(request.user)
    leave_req = get_object_or_404(
        LeaveRequest.objects.select_related(
            'personnel', 'leave_type', 'entitlement', 'requested_by', 'approved_by', 'processed_by',
            'routing_district', 'routing_region'
        ),
        pk=pk
    )

    # Security check: User can only see their own leave requests
    if (not personnel or leave_req.personnel_id != personnel.id) and leave_req.requested_by_id != request.user.id and not request.user.is_superuser:
        raise PermissionDenied("You do not have permission to view this leave record.")

    decisions = leave_req.decisions.all().select_related('approver').order_by('timestamp')
    workflow_history = leave_req.workflow_history.all().select_related('actor').order_by('timestamp')

    return render(request, "gnfs_komenda/self_service/leave_detail.html", {
        "personnel": personnel,
        "leave_req": leave_req,
        "decisions": decisions,
        "workflow_history": workflow_history,
    })


@login_required(login_url='login-staff')
def my_leave_edit_view(request, pk):
    """
    Correct and resubmit a request returned for correction.
    """
    personnel = get_personnel_for_user(request.user)
    leave_req = get_object_or_404(LeaveRequest, pk=pk)

    if (not personnel or leave_req.personnel_id != personnel.id) and not request.user.is_superuser:
        raise PermissionDenied("You can only edit your own leave requests.")

    if leave_req.status != LeaveRequest.Status.RETURNED_FOR_CORRECTION:
        messages.warning(request, f"This request is in status '{leave_req.get_status_display()}' and cannot be edited.")
        return redirect('my-leave-detail', pk=pk)

    if request.method == 'POST':
        start_date_str = request.POST.get('start_date')
        duration_str = request.POST.get('duration')
        reason = request.POST.get('reason', '').strip()
        contact_phone = request.POST.get('contact_phone', '').strip()
        contact_address = request.POST.get('contact_address', '').strip()
        handover_notes = request.POST.get('handover_notes', '').strip()
        med_ref = request.POST.get('medical_reference', '').strip()
        attachment = request.FILES.get('attachment') or leave_req.attachment

        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date() if start_date_str else leave_req.start_date
            duration = int(duration_str) if duration_str else leave_req.duration

            resubmit_corrected_leave_request(
                leave_req_id=pk,
                user=request.user,
                start_date=start_date,
                duration=duration,
                reason=reason,
                contact_phone=contact_phone,
                contact_address=contact_address,
                handover_notes=handover_notes,
                medical_reference=med_ref,
                attachment=attachment
            )
            messages.success(request, "Your corrected leave request has been resubmitted for processing.")
            return redirect('my-leave-detail', pk=pk)
        except (ValidationError, Exception) as e:
            messages.error(request, str(e))

    return render(request, "gnfs_komenda/self_service/leave_edit.html", {
        "personnel": personnel,
        "leave_req": leave_req,
    })


@login_required(login_url='login-staff')
def my_profile_view(request):
    """
    My Profile view for personal service and posting information.
    """
    personnel = get_personnel_for_user(request.user)
    if not personnel:
        return redirect('my-dashboard')

    command_appointment = personnel.current_command_appointment
    active_assignments = personnel.active_functional_assignments
    current_posting = personnel.current_posting

    return render(request, "gnfs_komenda/self_service/profile.html", {
        "personnel": personnel,
        "command_appointment": command_appointment,
        "active_assignments": active_assignments,
        "current_posting": current_posting,
    })


@login_required(login_url='login-staff')
def my_posting_view(request):
    """
    Current Command Posting & Career Posting History.
    """
    personnel = get_personnel_for_user(request.user)
    if not personnel:
        return redirect('my-dashboard')

    current_posting = personnel.current_posting
    posting_history = PersonnelPosting.objects.filter(
        personnel=personnel
    ).select_related('region', 'district', 'station', 'post', 'assigned_by').order_by('-start_date', '-created_at')

    return render(request, "gnfs_komenda/self_service/posting.html", {
        "personnel": personnel,
        "current_posting": current_posting,
        "posting_history": posting_history,
    })


@login_required(login_url='login-staff')
def my_departments_view(request):
    """
    Functional Assignments View (Primary & Additional units).
    """
    personnel = get_personnel_for_user(request.user)
    if not personnel:
        return redirect('my-dashboard')

    active_assignments = personnel.active_functional_assignments
    historical_assignments = PersonnelFunctionalAssignment.objects.filter(
        personnel=personnel,
        is_active=False
    ).select_related('functional_unit').order_by('-end_date', '-created_at')

    return render(request, "gnfs_komenda/self_service/departments.html", {
        "personnel": personnel,
        "active_assignments": active_assignments,
        "historical_assignments": historical_assignments,
    })


@login_required(login_url='login-staff')
def my_transfers_view(request):
    """
    Personal Transfer Status & Transfer History.
    """
    personnel = get_personnel_for_user(request.user)
    if not personnel:
        return redirect('my-dashboard')

    transfers = PersonnelTransfer.objects.filter(
        personnel=personnel
    ).select_related(
        'source_station', 'source_district', 'source_region',
        'destination_region', 'destination_district', 'destination_station'
    ).order_by('-created_at')

    active_transfer = transfers.filter(
        status__in=[
            TransferStatus.DRAFT, TransferStatus.PENDING_APPROVAL,
            TransferStatus.APPROVED, TransferStatus.RELEASED, TransferStatus.REPORTED
        ]
    ).first()

    return render(request, "gnfs_komenda/self_service/transfers.html", {
        "personnel": personnel,
        "active_transfer": active_transfer,
        "transfers": transfers,
    })


@login_required(login_url='login-staff')
def my_notifications_view(request):
    """
    Personal Notifications Center.
    """
    notifications = Notification.objects.filter(recipient=request.user).order_by('-created_at')

    mark_all = request.GET.get('mark_all_read')
    if mark_all:
        notifications.filter(is_read=False).update(is_read=True)
        messages.success(request, "All notifications marked as read.")
        return redirect('my-notifications')

    toggle_id = request.GET.get('toggle_read')
    if toggle_id:
        n = notifications.filter(id=toggle_id).first()
        if n:
            n.is_read = not n.is_read
            n.save(update_fields=['is_read'])
            return redirect('my-notifications')

    paginator = Paginator(notifications, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    unread_count = notifications.filter(is_read=False).count()

    return render(request, "gnfs_komenda/self_service/notifications.html", {
        "page_obj": page_obj,
        "unread_count": unread_count,
    })


@login_required(login_url='login-staff')
def my_profile_picture_upload_view(request):
    """
    Handle self-service profile picture upload, crop, and save for authenticated officer.
    """
    personnel = get_personnel_for_user(request.user)
    if not personnel:
        return JsonResponse({"success": False, "error": "No personnel record linked to your user account."}, status=400)

    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST method required."}, status=405)

    from gnfs_komenda.services.profile_picture_service import process_and_save_profile_picture
    image_data = request.POST.get("image_data") or request.FILES.get("profile_picture")

    if not image_data:
        return JsonResponse({"success": False, "error": "No image file or data provided."}, status=400)

    try:
        updated_p = process_and_save_profile_picture(personnel, image_data, actor=request.user)
        messages.success(request, "Profile photograph updated successfully.")
        return JsonResponse({
            "success": True,
            "url": updated_p.profile_picture.url,
            "message": "Profile photograph updated successfully."
        })
    except (ValidationError, ValueError) as e:
        err_msg = e.message if hasattr(e, 'message') else str(e)
        return JsonResponse({"success": False, "error": err_msg}, status=400)
    except Exception as e:
        return JsonResponse({"success": False, "error": f"Upload failed: {str(e)}"}, status=500)


@login_required(login_url='login-staff')
def personnel_profile_picture_upload_view(request, personnel_id):
    """
    Handle administrative profile picture upload, crop, and save within authorized scope.
    """
    personnel = get_object_or_404(NorminalRoll, id=personnel_id)

    from gnfs_komenda.services.profile_picture_service import can_user_manage_profile_picture, process_and_save_profile_picture
    if not can_user_manage_profile_picture(request.user, personnel):
        raise PermissionDenied("You do not have authorization to manage this officer's profile photograph.")

    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST method required."}, status=405)

    image_data = request.POST.get("image_data") or request.FILES.get("profile_picture")
    if not image_data:
        return JsonResponse({"success": False, "error": "No image file or data provided."}, status=400)

    try:
        updated_p = process_and_save_profile_picture(personnel, image_data, actor=request.user)
        messages.success(request, f"Profile photograph for {personnel.fullname} updated successfully.")
        return JsonResponse({
            "success": True,
            "url": updated_p.profile_picture.url,
            "message": f"Profile photograph for {personnel.fullname} updated successfully."
        })
    except (ValidationError, ValueError) as e:
        err_msg = e.message if hasattr(e, 'message') else str(e)
        return JsonResponse({"success": False, "error": err_msg}, status=400)
    except Exception as e:
        return JsonResponse({"success": False, "error": f"Upload failed: {str(e)}"}, status=500)

