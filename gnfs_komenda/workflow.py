"""
Approval Workflow & Audit Logging Service for GNFS Personnel Operations.
Handles tiered archiving requests: REQUEST -> DFO -> REGIONAL -> NATIONAL -> ARCHIVE.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone

from gnfs_komenda.models import (
    PersonnelActionRequest, ApprovalHistory, AuditLog, Notification, NorminalRoll
)
from gnfs_komenda.security import can_user_approve_request, can_user_manage_personnel, get_user_role

User = get_user_model()

SENSITIVE_KEYS = {
    'password', 'token', 'secret', 'otp', 'totp', 'account_number', 'auth_token', 'session'
}


def sanitize_values(val_dict):
    """Deep clean dictionary to ensure sensitive keys are never persisted to audit logs."""
    if not isinstance(val_dict, dict):
        return val_dict
    cleaned = {}
    for k, v in val_dict.items():
        if any(sensitive in k.lower() for sensitive in SENSITIVE_KEYS):
            cleaned[k] = "[REDACTED]"
        elif isinstance(v, dict):
            cleaned[k] = sanitize_values(v)
        else:
            cleaned[k] = str(v)
    return cleaned


def record_audit_log(
    user,
    action,
    target_type,
    target_id,
    description,
    old_values=None,
    new_values=None,
    region=None,
    district=None,
    station=None,
    post=None,
    ip_address=None
):
    """Create an immutable audit log entry."""
    return AuditLog.objects.create(
        user=user if user and user.is_authenticated else None,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        description=description,
        old_values=sanitize_values(old_values) if old_values else None,
        new_values=sanitize_values(new_values) if new_values else None,
        region=region,
        district=district,
        station=station,
        post=post,
        ip_address=ip_address
    )


def create_notification(recipient, title, message, notification_type="INFO", link_url=""):
    """Create an in-app notification for a user."""
    return Notification.objects.create(
        recipient=recipient,
        title=title,
        message=message,
        notification_type=notification_type,
        link_url=link_url
    )


def notify_role_recipients(role, title, message, region=None, district=None, link_url=""):
    """Send notifications to all active users matching a specific role and jurisdiction."""
    from staff.models import UserAssignment
    assignments = UserAssignment.objects.filter(role=role, active=True).select_related('user')
    if region:
        assignments = assignments.filter(region=region)
    if district:
        assignments = assignments.filter(district=district)

    for asgn in assignments:
        create_notification(
            recipient=asgn.user,
            title=title,
            message=message,
            notification_type="WORKFLOW",
            link_url=link_url
        )

    # Superusers also receive workflow alerts
    if role == "NATIONAL_ADMIN":
        for su in User.objects.filter(is_superuser=True, is_active=True):
            if not assignments.filter(user=su).exists():
                create_notification(
                    recipient=su,
                    title=title,
                    message=message,
                    notification_type="WORKFLOW",
                    link_url=link_url
                )


def create_archive_request(personnel, requester, reason, ip_address=None):
    """Initiate a personnel archive request through the approval pipeline."""
    if not can_user_manage_personnel(requester, personnel):
        raise PermissionDenied("You are not authorized to request actions for this personnel record.")

    # Self-deletion protection: User cannot archive their own matching personnel profile
    if requester.email and personnel.ghana_card and requester.email.lower() in personnel.fullname.lower():
        raise ValidationError("Self-deletion prohibited: You cannot request archival of your own personnel record.")

    # Check for active pending request
    existing_pending = PersonnelActionRequest.objects.filter(
        personnel=personnel,
        status__in=["PENDING_DFO", "PENDING_REGIONAL", "PENDING_NATIONAL"]
    ).exists()
    if existing_pending:
        raise ValidationError(f"A pending action request already exists for {personnel.fullname}.")

    with transaction.atomic():
        req = PersonnelActionRequest.objects.create(
            personnel=personnel,
            action_type="ARCHIVE",
            requested_by=requester,
            reason=reason,
            status="PENDING_DFO",
            current_approval_level="DFO"
        )

        record_audit_log(
            user=requester,
            action="ARCHIVE_REQUESTED",
            target_type="NorminalRoll",
            target_id=personnel.staffID,
            description=f"Archive request submitted for {personnel.fullname} ({personnel.staffID}). Reason: {reason}",
            region=personnel.current_region,
            district=personnel.current_district,
            station=personnel.station,
            post=personnel.post,
            ip_address=ip_address
        )

        # Notify DFO of the district
        p_district = personnel.current_district
        notify_role_recipients(
            role="DFO",
            title=f"New Personnel Archive Request: {personnel.fullname}",
            message=f"Archive request submitted by {requester.username} for {personnel.fullname} ({personnel.staffID}). Awaiting DFO review.",
            district=p_district,
            link_url=f"/approvals/{req.id}/"
        )

    return req


def process_approval_decision(request_obj, approver, decision, comment="", ip_address=None):
    """
    Process an approval step (APPROVED / REJECTED) with atomic transaction safety.
    Transitions:
    PENDING_DFO -> PENDING_REGIONAL -> PENDING_NATIONAL -> APPROVED (soft archives personnel).
    """
    if not can_user_approve_request(approver, request_obj):
        raise PermissionDenied("You do not have the required authority to review this request.")

    if request_obj.status in ("APPROVED", "REJECTED", "CANCELLED"):
        raise ValidationError("This request has already been finalized.")

    decision = decision.upper()
    if decision not in ("APPROVED", "REJECTED"):
        raise ValidationError("Invalid decision. Must be APPROVED or REJECTED.")

    with transaction.atomic():
        current_level = request_obj.current_approval_level
        personnel = request_obj.personnel

        # 1. Record History Entry
        ApprovalHistory.objects.create(
            request=request_obj,
            approver=approver,
            approval_level=current_level,
            decision=decision,
            comment=comment
        )

        # 2. Handle Rejection
        if decision == "REJECTED":
            request_obj.status = "REJECTED"
            request_obj.current_approval_level = "COMPLETED"
            request_obj.completed_at = timezone.now()
            request_obj.save()

            record_audit_log(
                user=approver,
                action=f"{current_level}_REJECTED",
                target_type="PersonnelActionRequest",
                target_id=request_obj.id,
                description=f"{current_level} rejected archive request for {personnel.fullname}. Comment: {comment}",
                region=personnel.current_region,
                district=personnel.current_district,
                station=personnel.station,
                post=personnel.post,
                ip_address=ip_address
            )

            create_notification(
                recipient=request_obj.requested_by,
                title=f"Archive Request Rejected: {personnel.fullname}",
                message=f"Your archive request for {personnel.fullname} was rejected at the {current_level} level by {approver.username}. Comment: {comment}",
                notification_type="ALERT",
                link_url=f"/approvals/{request_obj.id}/"
            )
            return request_obj

        # 3. Handle Approval
        if request_obj.status == "PENDING_DFO":
            request_obj.status = "PENDING_REGIONAL"
            request_obj.current_approval_level = "REGIONAL"
            request_obj.save()

            record_audit_log(
                user=approver,
                action="DFO_APPROVED",
                target_type="PersonnelActionRequest",
                target_id=request_obj.id,
                description=f"DFO approved archive request for {personnel.fullname}; forwarded to Regional Administration.",
                region=personnel.current_region,
                district=personnel.current_district,
                station=personnel.station,
                ip_address=ip_address
            )

            # Notify Regional Admin
            notify_role_recipients(
                role="REGIONAL_ADMIN",
                title=f"Pending Regional Approval: {personnel.fullname}",
                message=f"DFO approved archive request for {personnel.fullname}. Forwarded for Regional review.",
                region=personnel.current_region,
                link_url=f"/approvals/{request_obj.id}/"
            )

        elif request_obj.status == "PENDING_REGIONAL":
            request_obj.status = "PENDING_NATIONAL"
            request_obj.current_approval_level = "NATIONAL"
            request_obj.save()

            record_audit_log(
                user=approver,
                action="REGIONAL_APPROVED",
                target_type="PersonnelActionRequest",
                target_id=request_obj.id,
                description=f"Regional Administration approved archive request for {personnel.fullname}; forwarded to National Headquarters.",
                region=personnel.current_region,
                district=personnel.current_district,
                station=personnel.station,
                ip_address=ip_address
            )

            # Notify National Admins
            notify_role_recipients(
                role="NATIONAL_ADMIN",
                title=f"Pending National Approval: {personnel.fullname}",
                message=f"Regional Administration approved archive request for {personnel.fullname}. Awaiting final National HQ approval.",
                link_url=f"/approvals/{request_obj.id}/"
            )

        elif request_obj.status == "PENDING_NATIONAL":
            request_obj.status = "APPROVED"
            request_obj.current_approval_level = "COMPLETED"
            request_obj.completed_at = timezone.now()
            request_obj.save()

            # Execute Soft Archival
            personnel.is_active = False
            personnel.archived_at = timezone.now()
            personnel.archived_by = approver
            personnel.archive_reason = request_obj.reason
            personnel.save()

            record_audit_log(
                user=approver,
                action="NATIONAL_APPROVED",
                target_type="NorminalRoll",
                target_id=personnel.staffID,
                description=f"National Administration granted final approval. Personnel {personnel.fullname} ({personnel.staffID}) soft-archived.",
                region=personnel.current_region,
                district=personnel.current_district,
                station=personnel.station,
                post=personnel.post,
                ip_address=ip_address
            )

            create_notification(
                recipient=request_obj.requested_by,
                title=f"Personnel Archived: {personnel.fullname}",
                message=f"Final National approval granted. {personnel.fullname} ({personnel.staffID}) has been successfully archived.",
                notification_type="SUCCESS",
                link_url=f"/approvals/{request_obj.id}/"
            )

    return request_obj
