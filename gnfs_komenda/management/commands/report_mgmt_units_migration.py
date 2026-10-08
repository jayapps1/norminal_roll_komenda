"""
Audit & Migration Report for legacy mgmt_units field.
Evaluates existing values against verified GNFS Functional Organisational Units,
identifies safe mappings, and flags cost-centre strings as MANUAL REVIEW REQUIRED.
Never guesses or deletes existing data.
"""

from django.core.management.base import BaseCommand
from gnfs_komenda.models import NorminalRoll, FunctionalUnit


class Command(BaseCommand):
    help = "Audit and safely report/migrate legacy mgmt_units values to FunctionalUnit."

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Apply confident mappings to personnel functional_unit without overwriting mgmt_units.'
        )

    def handle(self, *args, **options):
        apply_changes = options.get('apply', False)

        self.stdout.write(self.style.SUCCESS("=" * 70))
        self.stdout.write(self.style.SUCCESS("GNFS LEGACY mgmt_units AUDIT & MIGRATION REPORT"))
        self.stdout.write(self.style.SUCCESS("=" * 70))

        records = NorminalRoll.objects.all()
        total_records = records.count()

        distinct_values = list(records.values_list('mgmt_units', flat=True).distinct())

        self.stdout.write(f"\nTotal Personnel Records: {total_records}")
        self.stdout.write(f"Distinct Legacy mgmt_units Values: {len(distinct_values)}\n")

        self.stdout.write(f"{'Legacy mgmt_units Value':<45} | {'Count':<6} | {'Mapping Status'}")
        self.stdout.write("-" * 85)

        mapped_count = 0
        manual_review_count = 0

        # Mapping dictionary for known unambiguous functional terms
        # If legacy value is purely a CAGD cost center code (e.g. 0307 GNFS, KEEA), flag for manual review.
        functional_units = {fu.name.lower(): fu for fu in FunctionalUnit.objects.all()}

        for val in distinct_values:
            val_str = str(val).strip() if val else ""
            count = records.filter(mgmt_units=val).count()

            # Attempt safe exact match or department/unit match
            matched_unit = None
            if val_str.lower() in functional_units:
                matched_unit = functional_units[val_str.lower()]

            if matched_unit:
                status = f"MAPPED -> {matched_unit.get_full_path()}"
                mapped_count += count
                if apply_changes:
                    records.filter(mgmt_units=val, functional_unit__isnull=True).update(functional_unit=matched_unit)
            else:
                status = "MANUAL REVIEW REQUIRED (Payroll Cost Centre / Unmapped)"
                manual_review_count += count

            self.stdout.write(f"{val_str:<45} | {count:<6} | {status}")

        self.stdout.write("-" * 85)
        self.stdout.write(f"Total Confident Mappings: {mapped_count}")
        self.stdout.write(f"Total Requiring Manual Review: {manual_review_count}")
        self.stdout.write("\nNOTE: Legacy 'mgmt_units' field is 100% preserved as the Government/CAGD Payroll Cost Centre.")
        if not apply_changes:
            self.stdout.write("Run with --apply to apply confident matches to the new functional_unit field.")
        else:
            self.stdout.write(self.style.SUCCESS("Confident mappings successfully applied."))
