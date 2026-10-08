from gnfs_komenda.models import Notification, PersonnelActionRequest
from gnfs_komenda.security import get_user_role, get_user_assignment
from gnfs_komenda.services.permission_resolver import get_available_workspaces, get_active_workspace


def gnfs_context(request):
    """Global context processor providing GNFS role, jurisdiction, pending notifications, and active workspaces."""
    if not request.user or not request.user.is_authenticated:
        return {
            'gnfs_role': None,
            'gnfs_assignment': None,
            'unread_notifications_count': 0,
            'pending_approvals_count': 0,
            'available_workspaces': [],
            'active_workspace': None,
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

    from gnfs_komenda.security import get_personnel_for_user
    user_personnel = get_personnel_for_user(request.user)
    workspaces = get_available_workspaces(request.user)
    active_ws = get_active_workspace(request)

    return {
        'gnfs_role': role,
        'gnfs_assignment': assignment,
        'gnfs_personnel': user_personnel,
        'unread_notifications_count': unread_count,
        'pending_approvals_count': pending_count,
        'available_workspaces': workspaces,
        'active_workspace': active_ws,
    }

