from django.core.management.base import BaseCommand
from gnfs_komenda.services.role_service import seed_roles_and_permissions


class Command(BaseCommand):
    help = "Seeds initial standard GNFS Application Roles and Permissions."

    def handle(self, *args, **options):
        self.stdout.write("Seeding GNFS Application Roles and Permissions...")
        res = seed_roles_and_permissions()
        roles_count = len(res["roles"])
        perms_count = res["permissions_count"]
        self.stdout.write(self.style.SUCCESS(
            f"Successfully seeded {roles_count} Application Roles and {perms_count} Permissions."
        ))
