"""
Centralized Data-Scope Security & Permission Layer for GNFS.
Enforces hierarchical jurisdiction filtering to prevent IDOR and unauthorized access.
"""

from django.db.models import Q
from gnfs_komenda.models import Region, District, Station, Post, NorminalRoll


def get_user_role(user):
    """Return the authoritative GNFS role for an authenticated user."""
    if not user or not user.is_authenticated:
        return None
    if user.is_superuser:
        return "NATIONAL_ADMIN"
    assignment = getattr(user, 'assignment', None)
    if assignment and assignment.active:
        return assignment.role
    if user.is_staff:
        return "NATIONAL_ADMIN"
    return "STAFF"


def get_user_assignment(user):
    """Return the UserAssignment object or None."""
    if not user or not user.is_authenticated:
        return None
    return getattr(user, 'assignment', None)


def get_personnel_queryset_for_user(user, include_archived=False):
    """
    Return the strictly scoped NorminalRoll QuerySet authorized for the user.
    Nationwide for National Admin; isolated to Region/District/Station/Post otherwise.
    """
    if not user or not user.is_authenticated:
        return NorminalRoll.objects.none()

    qs = NorminalRoll.objects.all() if include_archived else NorminalRoll.active_objects.all()
    qs = qs.select_related('station', 'station__district', 'station__district__region', 'post')

    # Superuser and National Admin see nationwide personnel
    if user.is_superuser:
        return qs

    assignment = get_user_assignment(user)
    if not assignment or not assignment.active:
        if user.is_staff:
            return qs
        return NorminalRoll.objects.none()

    role = assignment.role

    if role == "NATIONAL_ADMIN":
        return qs

    elif role == "REGIONAL_ADMIN":
        if not assignment.region:
            return NorminalRoll.objects.none()
        return qs.filter(station__district__region=assignment.region)

    elif role in ("DFO", "DISTRICT_ADMIN"):
        if not assignment.district:
            return NorminalRoll.objects.none()
        return qs.filter(station__district=assignment.district)

    elif role == "STATION_ADMIN":
        if not assignment.station:
            return NorminalRoll.objects.none()
        return qs.filter(station=assignment.station)

    elif role == "POST_ADMIN":
        if not assignment.post:
            return NorminalRoll.objects.none()
        return qs.filter(post=assignment.post)

    elif role == "STAFF":
        if assignment.post:
            return qs.filter(post=assignment.post)
        elif assignment.station:
            return qs.filter(station=assignment.station)
        elif assignment.district:
            return qs.filter(station__district=assignment.district)
        elif assignment.region:
            return qs.filter(station__district__region=assignment.region)
        return NorminalRoll.objects.none()

    return NorminalRoll.objects.none()


def get_regions_queryset_for_user(user):
    """Return Regions authorized for user."""
    if not user or not user.is_authenticated:
        return Region.objects.none()
    if user.is_superuser:
        return Region.objects.filter(active=True)

    assignment = get_user_assignment(user)
    if not assignment or not assignment.active:
        return Region.objects.filter(active=True) if user.is_staff else Region.objects.none()

    if assignment.role == "NATIONAL_ADMIN":
        return Region.objects.filter(active=True)
    elif assignment.region:
        return Region.objects.filter(id=assignment.region.id, active=True)
    return Region.objects.none()


def get_districts_queryset_for_user(user, region=None):
    """Return Districts authorized for user, optionally filtered by Region."""
    if not user or not user.is_authenticated:
        return District.objects.none()

    qs = District.objects.filter(active=True)
    if region:
        qs = qs.filter(region=region)

    if user.is_superuser:
        return qs

    assignment = get_user_assignment(user)
    if not assignment or not assignment.active:
        return qs if user.is_staff else District.objects.none()

    if assignment.role == "NATIONAL_ADMIN":
        return qs
    elif assignment.role == "REGIONAL_ADMIN":
        return qs.filter(region=assignment.region)
    elif assignment.district:
        return qs.filter(id=assignment.district.id)
    return District.objects.none()


def get_stations_queryset_for_user(user, district=None):
    """Return Stations authorized for user, optionally filtered by District."""
    if not user or not user.is_authenticated:
        return Station.objects.none()

    qs = Station.objects.filter(active=True)
    if district:
        qs = qs.filter(district=district)

    if user.is_superuser:
        return qs

    assignment = get_user_assignment(user)
    if not assignment or not assignment.active:
        return qs if user.is_staff else Station.objects.none()

    if assignment.role == "NATIONAL_ADMIN":
        return qs
    elif assignment.role == "REGIONAL_ADMIN":
        return qs.filter(district__region=assignment.region)
    elif assignment.role in ("DFO", "DISTRICT_ADMIN"):
        return qs.filter(district=assignment.district)
    elif assignment.station:
        return qs.filter(id=assignment.station.id)
    return Station.objects.none()


def get_posts_queryset_for_user(user, station=None):
    """Return Posts authorized for user, optionally filtered by Station."""
    if not user or not user.is_authenticated:
        return Post.objects.none()

    qs = Post.objects.filter(active=True)
    if station:
        qs = qs.filter(station=station)

    if user.is_superuser:
        return qs

    assignment = get_user_assignment(user)
    if not assignment or not assignment.active:
        return qs if user.is_staff else Post.objects.none()

    if assignment.role == "NATIONAL_ADMIN":
        return qs
    elif assignment.role == "REGIONAL_ADMIN":
        return qs.filter(station__district__region=assignment.region)
    elif assignment.role in ("DFO", "DISTRICT_ADMIN"):
        return qs.filter(station__district=assignment.district)
    elif assignment.role == "STATION_ADMIN":
        return qs.filter(station=assignment.station)
    elif assignment.post:
        return qs.filter(id=assignment.post.id)
    return Post.objects.none()


def can_user_manage_personnel(user, personnel):
    """Check if user is authorized to edit/view/manage this specific personnel record."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    scoped_qs = get_personnel_queryset_for_user(user, include_archived=True)
    return scoped_qs.filter(id=personnel.id).exists()


def can_user_approve_request(user, request_obj):
    """Verify if user has authority to approve/reject the given PersonnelActionRequest."""
    if not user or not user.is_authenticated:
        return False

    # Separation of duties: Cannot approve your own request
    if request_obj.requested_by_id == user.id:
        return False

    role = get_user_role(user)
    assignment = get_user_assignment(user)
    status = request_obj.status

    if user.is_superuser or role == "NATIONAL_ADMIN":
        return True

    personnel = request_obj.personnel
    p_region = personnel.current_region
    p_district = personnel.current_district

    if status == "PENDING_DFO":
        if role in ("DFO", "DISTRICT_ADMIN") and assignment and assignment.district == p_district:
            return True

    elif status == "PENDING_REGIONAL":
        if role == "REGIONAL_ADMIN" and assignment and assignment.region == p_region:
            return True

    elif status == "PENDING_NATIONAL":
        if role == "NATIONAL_ADMIN":
            return True

    return False
