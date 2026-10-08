"""
Posting Service for GNFS Personnel Command Assignments.
Manages career posting history, closing previous postings, and activating new postings.
"""

from django.db import transaction
from django.utils import timezone
from gnfs_komenda.models import PersonnelPosting, NorminalRoll


def close_active_posting(personnel, end_date=None, remarks=""):
    """
    Close any currently active command posting for an officer.
    """
    if not end_date:
        end_date = timezone.now().date()

    active_postings = PersonnelPosting.objects.filter(personnel=personnel, is_current=True)
    count = active_postings.update(
        is_current=False,
        end_date=end_date,
        remarks=remarks or "Posting concluded."
    )
    return count


def create_new_posting(personnel, region, district=None, station=None, post=None,
                       start_date=None, assigned_by=None, confirmed_by=None,
                       source_transfer=None, remarks=""):
    """
    Atomically close previous posting and activate new command posting.
    """
    if not start_date:
        start_date = timezone.now().date()

    with transaction.atomic():
        close_active_posting(personnel, end_date=start_date, remarks=remarks)

        posting = PersonnelPosting.objects.create(
            personnel=personnel,
            region=region,
            district=district,
            station=station,
            post=post,
            start_date=start_date,
            is_current=True,
            source_transfer=source_transfer,
            assigned_by=assigned_by,
            confirmed_by=confirmed_by,
            remarks=remarks
        )

        # Update NorminalRoll cached pointers
        personnel.station = station
        personnel.post = post
        personnel.save(update_fields=['station', 'post'])

        return posting


def get_officer_posting_history(personnel):
    """
    Return ordered list of command postings for an officer.
    """
    return PersonnelPosting.objects.filter(
        personnel=personnel
    ).select_related('region', 'district', 'station', 'post').order_by('-start_date', '-created_at')
