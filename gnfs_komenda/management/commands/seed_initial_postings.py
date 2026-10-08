"""
Seed Initial Personnel Postings for GNFS Personnel.
Creates an initial PersonnelPosting ledger record for all existing personnel
so that the historical posting foundation is established without changing any active assignments.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from gnfs_komenda.models import NorminalRoll, PersonnelPosting


class Command(BaseCommand):
    help = "Seed initial command postings for existing personnel without altering current records."

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("=" * 65))
        self.stdout.write(self.style.SUCCESS("GNFS INITIAL PERSONNEL POSTING BACKFILL"))
        self.stdout.write(self.style.SUCCESS("=" * 65))

        created_count = 0
        existing_count = 0
        skipped_count = 0

        with transaction.atomic():
            for officer in NorminalRoll.objects.select_related('station', 'station__district', 'station__district__region', 'post').all():
                if not officer.station or not officer.station.district or not officer.station.district.region:
                    skipped_count += 1
                    continue

                # Check if officer already has an active posting
                has_active = PersonnelPosting.objects.filter(personnel=officer, is_current=True).exists()
                if has_active:
                    existing_count += 1
                    continue

                start_date = officer.created_at.date() if officer.created_at else timezone.now().date()
                PersonnelPosting.objects.create(
                    personnel=officer,
                    region=officer.station.district.region,
                    district=officer.station.district,
                    station=officer.station,
                    post=officer.post,
                    start_date=start_date,
                    is_current=True,
                    remarks="Initial posting backfilled from baseline personnel record."
                )
                created_count += 1

        self.stdout.write(f"\nTotal personnel processed: {NorminalRoll.objects.count()}")
        self.stdout.write(f"Initial postings created: {created_count}")
        self.stdout.write(f"Already had active posting: {existing_count}")
        self.stdout.write(f"Skipped (unassigned station): {skipped_count}")
        self.stdout.write(self.style.SUCCESS("Personnel posting backfill completed successfully."))
