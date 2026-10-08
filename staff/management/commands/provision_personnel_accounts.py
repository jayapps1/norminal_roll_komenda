from django.core.management.base import BaseCommand
from staff.services.account_provisioning_service import bulk_provision_personnel_accounts


class Command(BaseCommand):
    help = "Bulk provision Staff ID user accounts with initial temporary credentials for personnel."

    def add_arguments(self, parser):
        parser.add_argument(
            "--temporary-password",
            default=None,
            help="Override default temporary password for provisioned accounts.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate account provisioning without committing changes to database.",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        temp_pw = options.get("temporary_password")

        self.stdout.write("GNFS Personnel Account Provisioning")
        self.stdout.write("====================================")
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN MODE — No database records will be modified.\n"))

        results = bulk_provision_personnel_accounts(
            temporary_password=temp_pw,
            dry_run=dry_run
        )

        self.stdout.write(f"Personnel Examined:          {results['examined']:>4}")
        self.stdout.write(f"Accounts Created:            {results['created']:>4}")
        self.stdout.write(f"Already Linked:              {results['already_linked']:>4}")
        self.stdout.write(f"Existing Accounts Skipped:   {results['existing_skipped']:>4}")
        self.stdout.write(f"System Accounts Skipped:     {results['system_skipped']:>4}")
        self.stdout.write(f"Errors:                      {results['errors']:>4}")

        if results["details"]:
            self.stdout.write("\nError Details:")
            for err in results["details"]:
                self.stdout.write(self.style.ERROR(f" - {err}"))

        self.stdout.write(self.style.SUCCESS("\nProvisioning execution completed successfully."))
