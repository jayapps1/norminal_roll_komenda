"""
Watch Room Service for GNFS.
Implements:
- Administrative release / dispatch to Watch Room (SENT_TO_WATCH_ROOM).
- Official Station Diary / Leave Book entry recording (BOOKED).
- Operational resumption tracking (DUE_TO_RESUME -> RESUMED / COMPLETED).
- Personnel AT POST restoration upon resumption.
- Strict security constraints:
  * Watch Room CANNOT approve or reject.
  * Watch Room CANNOT alter approved duration, dates, or leave type.
  * Zero balance deductions happen in Watch Room.
"""

import logging
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone

from gnfs_komenda.models import (
    LeaveRequest,
    WatchRoomBooking,
    LeaveApprovalDecision,
    LeaveWorkflowHistory,
    NorminalRoll,
    AuditLog,
)
from gnfs_komenda.security import get_user_role, send_notification
from gnfs_komenda.services.functional_scope_service import get_functional_scope

logger = logging.getLogger(__name__)


def can_user_release_to_watch_room(user, leave_req):
    """
    Check if user is authorized Administration to release approved leave to Watch Room.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True

    adm_scope = get_functional_scope(user, "ADMINISTRATION")
    if not adm_scope["authorized"]:
        return False

    level = adm_scope.get("level", "DISTRICT")
    if level == "NATIONAL":
        return True
    elif level == "REGIONAL":
        return bool(leave_req.routing_region and adm_scope.get("region") == leave_req.routing_region)
    elif level == "DISTRICT":
        return bool(leave_req.routing_district and adm_scope.get("district") == leave_req.routing_district)

    return False


def can_user_book_watch_room(user, leave_req):
    """
    Check if user has Watch Room operational authority to log the approved leave in Station Diary.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True

    # Check Watch Room functional scope
    wr_scope = get_functional_scope(user, "WATCH_ROOM")
    if wr_scope["authorized"]:
        level = wr_scope.get("level", "DISTRICT")
        if level == "NATIONAL":
            return True
        elif level == "REGIONAL":
            return bool(leave_req.routing_region and wr_scope.get("region") == leave_req.routing_region)
        elif level == "DISTRICT":
            return bool(leave_req.routing_district and wr_scope.get("district") == leave_req.routing_district)

    # Fallback: Station officer stationed at the same station
    user_personnel = NorminalRoll.objects.filter(staffID__iexact=user.username).first()
    if user_personnel and leave_req.personnel:
        if user_personnel.station and leave_req.personnel.station:
            if user_personnel.station_id == leave_req.personnel.station_id:
                return True

    return False


def admin_release_to_watch_room(leave_req_id, user, comments=""):
    """
    District Administration sends approved leave to Watch Room for Station Diary entry.
    Status moves: APPROVED_PENDING_ADMIN_PROCESSING -> SENT_TO_WATCH_ROOM.
    """
    with transaction.atomic():
        leave_req = LeaveRequest.objects.select_for_update().select_related(
            'personnel', 'leave_type', 'routing_district', 'routing_region'
        ).get(pk=leave_req_id)

        if not can_user_release_to_watch_room(user, leave_req):
            raise PermissionDenied("You do not have administrative authority to dispatch this leave to the Watch Room.")

        valid_source_statuses = (
            LeaveRequest.Status.APPROVED_PENDING_ADMIN_PROCESSING,
            LeaveRequest.Status.PENDING_ADMIN_FINAL_PROCESSING,
            LeaveRequest.Status.APPROVED,
        )
        if leave_req.status not in valid_source_statuses:
            raise ValidationError(
                f"Leave request #{leave_req.id} is in status '{leave_req.get_status_display()}'; only command-approved leaves can be sent to Watch Room."
            )

        old_status = leave_req.status
        leave_req.status = LeaveRequest.Status.SENT_TO_WATCH_ROOM
        leave_req.processing_stage = LeaveRequest.ProcessingStage.WATCH_ROOM
        leave_req.sent_to_watch_room_by = user
        leave_req.sent_to_watch_room_at = timezone.now()
        leave_req.save(update_fields=[
            'status', 'processing_stage', 'sent_to_watch_room_by',
            'sent_to_watch_room_at', 'updated_at'
        ])

        # Record decision & audit
        LeaveApprovalDecision.objects.create(
            leave_request=leave_req,
            approver=user,
            approver_role="ADMINISTRATION",
            decision="SENT_TO_WATCH_ROOM",
            comments=comments or "Dispatched from District Administration to Watch Room for Station Diary entry.",
            previous_status=old_status,
            new_status=leave_req.status
        )

        LeaveWorkflowHistory.objects.create(
            leave_request=leave_req,
            action="SENT_TO_WATCH_ROOM",
            from_status=old_status,
            to_status=leave_req.status,
            actor=user,
            actor_role="District Administrator",
            functional_office="Administration",
            comment=comments or f"Sent to Watch Room. Reference: {leave_req.reference_number or leave_req.id}"
        )

        AuditLog.objects.create(
            user=user,
            action="DISPATCH_TO_WATCH_ROOM",
            target_type="LeaveRequest",
            target_id=str(leave_req.id),
            description=f"Approved leave #{leave_req.reference_number or leave_req.id} dispatched to Watch Room for Station Diary entry.",
            region=leave_req.routing_region,
            district=leave_req.routing_district,
            station=leave_req.personnel.station if leave_req.personnel else None,
        )

        return leave_req


def watch_room_book_leave(leave_req_id, user, book_type="STATION_DIARY", book_reference="", remarks=""):
    """
    District Watch Room official operational recording of approved leave (Prompt 3 Sections 16-17).
    Status moves: SENT_TO_WATCH_ROOM -> BOOKED (or ACTIVE if already starting today).

    Strict constraints:
    - Watch Room CANNOT approve or reject.
    - Watch Room CANNOT alter duration or dates.
    - Zero balance deduction.
    """
    if not book_type:
        book_type = "STATION_DIARY"

    with transaction.atomic():
        leave_req = LeaveRequest.objects.select_for_update().select_related(
            'personnel', 'leave_type', 'routing_district', 'routing_region'
        ).get(pk=leave_req_id)

        if not can_user_book_watch_room(user, leave_req):
            raise PermissionDenied("You do not have Watch Room authority to book this leave record.")

        if leave_req.status != LeaveRequest.Status.SENT_TO_WATCH_ROOM:
            raise ValidationError(
                f"Cannot book leave in status '{leave_req.get_status_display()}'. Must be dispatched by Administration (SENT_TO_WATCH_ROOM)."
            )

        old_status = leave_req.status
        today = timezone.localdate()

        # Update Watch Room fields on LeaveRequest
        leave_req.watch_room_booked_by = user
        leave_req.watch_room_booked_at = timezone.now()
        leave_req.watch_room_book_type = book_type
        leave_req.watch_room_book_reference = book_reference
        leave_req.watch_room_remarks = remarks

        # Status transition: ACTIVE if today is within leave period, else BOOKED
        if leave_req.start_date <= today <= leave_req.last_leave_date:
            leave_req.status = LeaveRequest.Status.ACTIVE
            if leave_req.personnel:
                NorminalRoll.objects.filter(pk=leave_req.personnel_id).update(
                    leave_status="ON LEAVE",
                    leave_type=leave_req.leave_type.name,
                    start_leave=leave_req.start_date,
                    end_leave=leave_req.last_leave_date
                )
        else:
            leave_req.status = LeaveRequest.Status.BOOKED

        leave_req.processing_stage = LeaveRequest.ProcessingStage.COMPLETED
        leave_req.save()

        # Create or update official WatchRoomBooking record
        WatchRoomBooking.objects.update_or_create(
            leave_request=leave_req,
            defaults={
                'district': leave_req.routing_district,
                'booked_by': user,
                'booked_at': timezone.now(),
                'book_type': book_type,
                'book_reference': book_reference,
                'remarks': remarks,
            }
        )

        # Record decision & history
        LeaveApprovalDecision.objects.create(
            leave_request=leave_req,
            approver=user,
            approver_role="WATCH_ROOM",
            decision="BOOKED",
            comments=f"Logged in {book_type} (Ref: {book_reference}). {remarks}".strip(),
            previous_status=old_status,
            new_status=leave_req.status
        )

        LeaveWorkflowHistory.objects.create(
            leave_request=leave_req,
            action="WATCH_ROOM_BOOKED",
            from_status=old_status,
            to_status=leave_req.status,
            actor=user,
            actor_role="Watch Room Officer",
            functional_office="Watch Room",
            comment=f"Logged in {book_type} (Ref: {book_reference}). Remarks: {remarks}".strip()
        )

        AuditLog.objects.create(
            user=user,
            action="WATCH_ROOM_BOOKING",
            target_type="LeaveRequest",
            target_id=str(leave_req.id),
            description=f"Leave #{leave_req.reference_number or leave_req.id} logged in {book_type} (Ref: {book_reference}) by {user.username}.",
            region=leave_req.routing_region,
            district=leave_req.routing_district,
            station=leave_req.personnel.station if leave_req.personnel else None,
        )

        # Notify applicant
        if leave_req.requested_by:
            send_notification(
                recipient=leave_req.requested_by,
                title="Leave Booked in Watch Room",
                message=f"Your approved leave #{leave_req.reference_number or leave_req.id} was booked in the Station Diary (Ref: {book_reference}).",
                notification_type="SUCCESS",
                link_url=f"/my/leave/{leave_req.id}/"
            )

        return leave_req


def watch_room_record_resumption(leave_req_id, user, resumed_at=None, remarks=""):
    """
    Watch Room records official resumption / return to post (Prompt 3 Section 24).
    Status moves: BOOKED / ACTIVE / DUE_TO_RESUME -> RESUMED / COMPLETED.
    Restores Personnel status to 'AT POST'.
    Does NOT alter approved leave dates.
    """
    with transaction.atomic():
        leave_req = LeaveRequest.objects.select_for_update().select_related(
            'personnel', 'leave_type', 'routing_district', 'routing_region'
        ).get(pk=leave_req_id)

        if not can_user_book_watch_room(user, leave_req):
            raise PermissionDenied("You do not have Watch Room authority to record resumption.")

        valid_resumption_statuses = (
            LeaveRequest.Status.BOOKED,
            LeaveRequest.Status.ACTIVE,
            LeaveRequest.Status.DUE_TO_RESUME,
            LeaveRequest.Status.APPROVED,
        )
        if leave_req.status not in valid_resumption_statuses:
            raise ValidationError(
                f"Cannot record resumption for leave in status '{leave_req.get_status_display()}'. Must be active or booked."
            )

        old_status = leave_req.status
        now_dt = resumed_at or timezone.now()

        leave_req.status = LeaveRequest.Status.RESUMED
        leave_req.resumed_at = now_dt
        leave_req.resumed_by = user
        leave_req.resumption_remarks = remarks
        leave_req.save(update_fields=['status', 'resumed_at', 'resumed_by', 'resumption_remarks', 'updated_at'])

        # Update WatchRoomBooking record if exists
        w_booking = getattr(leave_req, 'watch_room_booking_record', None)
        if w_booking:
            w_booking.resumed_at = now_dt
            w_booking.resumed_by = user
            w_booking.resumption_remarks = remarks
            w_booking.save(update_fields=['resumed_at', 'resumed_by', 'resumption_remarks'])

        # Restore Personnel status to 'AT POST'
        personnel = leave_req.personnel
        if leave_req.personnel_id:
            NorminalRoll.objects.filter(pk=leave_req.personnel_id).update(
                leave_status="AT POST",
                leave_type="AT POST",
                start_leave=None,
                end_leave=None,
            )

        # Record decision & workflow history
        LeaveApprovalDecision.objects.create(
            leave_request=leave_req,
            approver=user,
            approver_role="WATCH_ROOM",
            decision="RESUMED",
            comments=f"Official resumption recorded at {now_dt}. Remarks: {remarks}".strip(),
            previous_status=old_status,
            new_status=leave_req.status
        )

        LeaveWorkflowHistory.objects.create(
            leave_request=leave_req,
            action="RESUMPTION_RECORDED",
            from_status=old_status,
            to_status=leave_req.status,
            actor=user,
            actor_role="Watch Room Officer",
            functional_office="Watch Room",
            comment=f"Official resumption recorded. Remarks: {remarks}".strip()
        )

        AuditLog.objects.create(
            user=user,
            action="RECORD_RESUMPTION",
            target_type="LeaveRequest",
            target_id=str(leave_req.id),
            description=f"Resumption recorded for {personnel.fullname} ({personnel.staffID}) on Leave #{leave_req.id}.",
            region=leave_req.routing_region,
            district=leave_req.routing_district,
            station=personnel.station if personnel else None,
        )

        return leave_req
