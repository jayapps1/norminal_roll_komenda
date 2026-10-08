from django.core.management.base import BaseCommand
from gnfs_komenda.services.functional_office_service import seed_all_functional_offices


class Command(BaseCommand):
    help = "Seeds authoritative GNFS Functional Offices across National, Regional, and District levels with fallback configuration."

    def handle(self, *args, **options):
        self.stdout.write("Seeding GNFS Functional Offices (Administration, Accounts, Operations, HR, Watch Room)...")
        res = seed_all_functional_offices()
        self.stdout.write(self.style.SUCCESS(
            f"Successfully seeded/verified {res['total_offices']} Functional Offices across {res['districts_processed']} Districts."
        ))
