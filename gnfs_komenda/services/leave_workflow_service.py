"""
Master Leave Workflow Service for GNFS.
Orchestrates the District Fire Station leave workflow:
PERSONNEL -> ADMINISTRATION -> OPERATIONS -> DFO/MFO -> ADMIN FINAL PROCESSING -> WATCH ROOM -> RESUMPTION

Core constraints:
- ONE LeaveRequest travels through the entire workflow. ZERO duplicate leave request objects.
- Personnel Portal: user is derived from request.user -> Personnel.
- Administration verifies and routes based on active functional assignment (Operations Personnel -> Operations).
- Operations recommends (NEVER final approves, zero balance deduction).
- DFO/MFO performs final command approval/rejection (Anti-self-approval enforced).
- Deducts balance exactly ONCE upon DFO/MFO command approval (select_for_update()).
- Watch Room books in Station Diary (CANNOT alter duration, dates, or approve/reject).
- Watch Room tracks and records resumption, returning Personnel to AT POST.
- Complete audit trail preserved in LeaveWorkflowHistory on every transition.
"""

import logging
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone

from gnfs_komenda.models import (
    LeaveRequest,
    LeaveApprovalDecision,
    LeaveWorkflowHistory,
    NorminalRoll,
    District,
    Region,
)
from gnfs_komenda.services.leave_routing_service import (
    is_operations_personnel,
    resolve_leave_route,
    submit_personnel_leave_request,
    process_leave_request as _routing_admin_process,
)
from gnfs_komenda.services.leave_recommendation_service import (
    operations_recommend_leave as _ops_recommend,
    can_user_recommend_leave,
)
from gnfs_komenda.services.leave_approval_service import (
    dfo_approve_leave as _dfo_approve,
    dfo_reject_leave as _dfo_reject,
    is_self_approval,
    can_user_approve_leave,
)
from gnfs_komenda.services.watch_room_service import (
    admin_release_to_watch_room as _wr_release,
    watch_room_book_leave as _wr_book,
    watch_room_record_resumption as _wr_resumption,
    can_user_release_to_watch_room,
    can_user_book_watch_room,
)
from gnfs_komenda.services.leave_balance_service import (
    deduct_leave_balance_atomic,
    release_reserved_leave_days,
    get_base_annual_entitlement,
    calculate_proportionate_entitlement,
)

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------------------
# 1. PERSONNEL SUBMISSION (Section 1-3)
# ------------------------------------------------------------------------------
def submit_district_leave(personnel, leave_type, start_date, duration,
                          requested_by, reason="", contact_phone="",
                          contact_address="", handover_notes="",
                          medical_reference="", attachment=None,
                          is_proportionate=False):
    """
    Personnel submits leave from My Portal (Prompt 3 Sections 2-3).
    Personnel is derived from request.user -> Personnel.
    Status starts at: PENDING_ADMIN_REVIEW.
    Appears in Administration Console Leave Inbox.
    """
    return submit_personnel_leave_request(
        personnel=personnel,
        leave_type=leave_type,
        start_date=start_date,
        duration=duration,
        requested_by=requested_by,
        reason=reason,
        contact_phone=contact_phone,
        contact_address=contact_address,
        handover_notes=handover_notes,
        medical_reference=medical_reference,
        attachment=attachment,
        is_proportionate=is_proportionate,
        is_admin_created=False
    )


# ------------------------------------------------------------------------------
# 2. ADMINISTRATION REVIEW & FORWARDING (Section 4-6)
# ------------------------------------------------------------------------------
def admin_process_leave(leave_req_id, user, action, comments=""):
    """
    Administration review step (Prompt 3 Sections 4-6).
    Actions:
    - FORWARD_TO_OPERATIONS: Forwards to Operations if officer has active Operations assignment.
    - RETURN_FOR_CORRECTION: Returns request to officer with required changes.
    Administration CANNOT finally approve.
    """
    return _routing_admin_process(leave_req_id, user, action, comments=comments)


# ------------------------------------------------------------------------------
# 3. OPERATIONS RECOMMENDATION (Section 7-10)
# ------------------------------------------------------------------------------
def operations_recommend_leave(leave_req_id, user, recommendation, comments=""):
    """
    District Operations recommendation step (Prompt 3 Sections 7-10).
    Actions: RECOMMEND, NOT_RECOMMEND, RETURN_FOR_CORRECTION.
    Operations CANNOT finally approve.
    Zero balance deduction.
    Status advances to: PENDING_DFO_MFO_DECISION on RECOMMEND / NOT_RECOMMEND.
    """
    return _ops_recommend(leave_req_id, user, recommendation, comments=comments)


# ------------------------------------------------------------------------------
# 4. DFO / MFO COMMAND DECISION (Section 11-13)
# ------------------------------------------------------------------------------
def command_approve_leave(leave_req_id, user, comments=""):
    """
    DFO / MFO Final Command Approval (Prompt 3 Sections 11-13).
    Strict anti-self-approval enforced.
    Deducts balance exactly once atomically.
    Status advances to: APPROVED_PENDING_ADMIN_PROCESSING.
    Notifies: Personnel, Administration, Operations, District 2IC.
    """
    return _dfo_approve(leave_req_id, user, comments=comments)


def command_reject_leave(leave_req_id, user, comments):
    """
    DFO / MFO Final Command Rejection (Prompt 3 Sections 11-12).
    Requires written comments. Releases reserved balance.
    Status advances to: REJECTED.
    """
    return _dfo_reject(leave_req_id, user, comments=comments)


# ------------------------------------------------------------------------------
# 5. ADMINISTRATION DISPATCH TO WATCH ROOM (Section 14-15)
# ------------------------------------------------------------------------------
def admin_send_to_watch_room(leave_req_id, user, comments=""):
    """
    District Administration sends approved leave to Watch Room (Prompt 3 Sections 14-15).
    Status advances to: SENT_TO_WATCH_ROOM.
    Notifies Watch Room.
    """
    return _wr_release(leave_req_id, user, comments=comments)


# ------------------------------------------------------------------------------
# 6. WATCH ROOM BOOKING (Section 16-17)
# ------------------------------------------------------------------------------
def watch_room_book_leave(leave_req_id, user, book_type="STATION_DIARY",
                          book_reference="", remarks=""):
    """
    Watch Room logs approved leave in Station Diary (Prompt 3 Sections 16-17).
    Status advances to: BOOKED (or ACTIVE if already starting).
    Zero balance deductions.
    Watch Room cannot alter dates or duration.
    """
    return _wr_book(leave_req_id, user, book_type=book_type,
                    book_reference=book_reference, remarks=remarks)


# ------------------------------------------------------------------------------
# 7. WATCH ROOM RESUMPTION (Section 24-25)
# ------------------------------------------------------------------------------
def watch_room_record_resumption(leave_req_id, user, resumed_at=None, remarks=""):
    """
    Watch Room records official resumption (Prompt 3 Sections 24-25).
    Status advances to: RESUMED / COMPLETED.
    Personnel restored to 'AT POST'.
    Original approved leave dates remain unaltered.
    """
    return _wr_resumption(leave_req_id, user, resumed_at=resumed_at, remarks=remarks)


# ------------------------------------------------------------------------------
# 8. PERSONNEL STATUS TRACKER (Section 28)
# ------------------------------------------------------------------------------
def get_personal_status_tracker(leave_req):
    """
    Provides the visual stepper model for Personal Portal (Prompt 3 Section 28):
    1. Submitted
    2. Administration Reviewed
    3. Operations Recommended
    4. DFO Approved
    5. Administration Processed
    6. Sent to Watch Room
    7. Booked
    8. Resumed / Completed
    """
    try:
        leave_req.refresh_from_db()
    except Exception:
        pass
    status = leave_req.status
    histories = leave_req.workflow_history.all().order_by('timestamp')
    actions = {h.action for h in histories}

    has_submitted = True
    has_admin_reviewed = (
        bool(leave_req.processed_at) or
        "ADMIN_REVIEWED_AND_FORWARDED" in actions or
        "FORWARDED_TO_OPERATIONS" in actions or
        status not in (LeaveRequest.Status.DRAFT, LeaveRequest.Status.SUBMITTED, LeaveRequest.Status.PENDING_ADMIN_REVIEW)
    )
    has_ops_recommended = (
        bool(leave_req.operations_recommended_at) or
        "OPERATIONS_RECOMMENDED" in actions or
        "OPERATIONS_NOT_RECOMMENDED" in actions
    )
    has_dfo_approved = (
        bool(leave_req.approved_at) or
        "COMMAND_APPROVED" in actions or
        "APPROVED" in actions or
        status in (LeaveRequest.Status.APPROVED_PENDING_ADMIN_PROCESSING,
                   LeaveRequest.Status.SENT_TO_WATCH_ROOM,
                   LeaveRequest.Status.BOOKED,
                   LeaveRequest.Status.ACTIVE,
                   LeaveRequest.Status.DUE_TO_RESUME,
                   LeaveRequest.Status.RESUMED,
                   LeaveRequest.Status.COMPLETED)
    ) and status not in (LeaveRequest.Status.REJECTED, LeaveRequest.Status.RETURNED_FOR_CORRECTION)
    has_sent_watch_room = (
        bool(leave_req.sent_to_watch_room_at) or
        "SENT_TO_WATCH_ROOM" in actions or
        status in (LeaveRequest.Status.SENT_TO_WATCH_ROOM, LeaveRequest.Status.BOOKED,
                   LeaveRequest.Status.ACTIVE, LeaveRequest.Status.DUE_TO_RESUME,
                   LeaveRequest.Status.RESUMED, LeaveRequest.Status.COMPLETED)
    )
    has_booked = (
        bool(leave_req.watch_room_booked_at) or
        "WATCH_ROOM_BOOKED" in actions or
        status in (LeaveRequest.Status.BOOKED, LeaveRequest.Status.ACTIVE,
                   LeaveRequest.Status.DUE_TO_RESUME, LeaveRequest.Status.RESUMED,
                   LeaveRequest.Status.COMPLETED)
    )
    has_resumed = (
        bool(leave_req.resumed_at) or
        "RESUMPTION_RECORDED" in actions or
        status in (LeaveRequest.Status.RESUMED, LeaveRequest.Status.COMPLETED)
    )

    # Determine user-facing current status summary
    today = timezone.localdate()
    if status == LeaveRequest.Status.REJECTED:
        current_label = "REJECTED"
        badge_class = "bg-danger"
    elif status == LeaveRequest.Status.RETURNED_FOR_CORRECTION:
        current_label = "RETURNED FOR CORRECTION"
        badge_class = "bg-warning text-dark"
    elif status in (LeaveRequest.Status.RESUMED, LeaveRequest.Status.COMPLETED):
        current_label = "RESUMED / COMPLETED"
        badge_class = "bg-secondary"
    elif status == LeaveRequest.Status.ACTIVE or (has_booked and leave_req.start_date <= today <= leave_req.last_leave_date):
        current_label = "ACTIVE / ON LEAVE"
        badge_class = "bg-success"
    elif has_booked or has_dfo_approved:
        if leave_req.start_date > today:
            current_label = "APPROVED / UPCOMING LEAVE"
            badge_class = "bg-primary"
        else:
            current_label = "APPROVED"
            badge_class = "bg-success"
    elif status == LeaveRequest.Status.PENDING_OPERATIONS_RECOMMENDATION:
        current_label = "WAITING FOR OPERATIONS RECOMMENDATION"
        badge_class = "bg-info text-dark"
    elif status == LeaveRequest.Status.PENDING_DFO_MFO_DECISION:
        current_label = "WAITING FOR DFO/MFO DECISION"
        badge_class = "bg-primary"
    elif status in (LeaveRequest.Status.PENDING_ADMIN_REVIEW, LeaveRequest.Status.SUBMITTED):
        current_label = "WAITING FOR ADMINISTRATION REVIEW"
        badge_class = "bg-warning text-dark"
    else:
        current_label = leave_req.get_status_display()
        badge_class = "bg-secondary"

    steps = [
        {"name": "Submitted", "completed": has_submitted, "timestamp": leave_req.created_at},
        {"name": "Administration Reviewed", "completed": has_admin_reviewed, "timestamp": leave_req.processed_at},
        {"name": "Operations Recommended", "completed": has_ops_recommended, "timestamp": leave_req.operations_recommended_at},
        {"name": "DFO Approved", "completed": has_dfo_approved, "timestamp": leave_req.approved_at},
        {"name": "Administration Processed", "completed": has_sent_watch_room, "timestamp": leave_req.sent_to_watch_room_at},
        {"name": "Sent to Watch Room", "completed": has_sent_watch_room, "timestamp": leave_req.sent_to_watch_room_at},
        {"name": "Booked", "completed": has_booked, "timestamp": leave_req.watch_room_booked_at},
        {"name": "Resumed Duty", "completed": has_resumed, "timestamp": leave_req.resumed_at},
    ]

    return {
        "reference_number": leave_req.reference_number or f"LEV-{leave_req.id}",
        "current_status_label": current_label,
        "badge_class": badge_class,
        "steps": steps,
        "is_active_on_leave": (leave_req.start_date <= today <= leave_req.last_leave_date and has_booked),
    }
