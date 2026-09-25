from gnfs_komenda.models import Notification, PersonnelActionRequest
from gnfs_komenda.security import get_user_role, get_user_assignment


def gnfs_context(request):
    """Global context processor providing GNFS role, jurisdiction, and pending notifications."""
    if not request.user or not request.user.is_authenticated:
        return {
            'gnfs_role': None,
            'gnfs_assignment': None,
            'unread_notifications_count': 0,
            'pending_approvals_count': 0,
        }

    role = get_user_role(request.user)
    assignment = get_user_assignment(request.user)
    unread_count = Notification.objects.filter(recipient=request.user, is_read=False).count()

    # Calculate pending approval requests relevant to this user
    pending_count = 0
    if request.user.is_superuser or role == "NATIONAL_ADMIN":
        pending_count = PersonnelActionRequest.objects.filter(status="PENDING_NATIONAL").count()
    elif role == "REGIONAL_ADMIN" and assignment and assignment.region:
        pending_count = PersonnelActionRequest.objects.filter(
            status="PENDING_REGIONAL",
            personnel__station__district__region=assignment.region
        ).count()
    elif role in ("DFO", "DISTRICT_ADMIN") and assignment and assignment.district:
        pending_count = PersonnelActionRequest.objects.filter(
            status="PENDING_DFO",
            personnel__station__district=assignment.district
        ).count()

    return {
        'gnfs_role': role,
        'gnfs_assignment': assignment,
        'unread_notifications_count': unread_count,
        'pending_approvals_count': pending_count,
    }
