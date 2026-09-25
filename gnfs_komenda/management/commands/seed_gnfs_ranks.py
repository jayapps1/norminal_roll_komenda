from django.core.management.base import BaseCommand
from django.db import transaction
from gnfs_komenda.models import Rank, NorminalRoll


SENIOR_RANKS = [
    {"order": 1, "name": "Chief Fire Officer", "abbreviation": "CFO"},
    {"order": 2, "name": "Deputy Chief Fire Officer", "abbreviation": "DCFO"},
    {"order": 3, "name": "Assistant Chief Fire Officer", "abbreviation": "ACFO"},
    {"order": 4, "name": "Divisional Officer Grade One", "abbreviation": "DO I"},
    {"order": 5, "name": "Divisional Officer Grade Two", "abbreviation": "DO II"},
    {"order": 6, "name": "Divisional Officer Grade Three", "abbreviation": "DO III"},
    {"order": 7, "name": "Assistant Divisional Officer Grade One", "abbreviation": "ADO I"},
    {"order": 8, "name": "Assistant Divisional Officer Grade Two", "abbreviation": "ADO II"},
    {"order": 9, "name": "Cadet Officer", "abbreviation": "CO"},
]

JUNIOR_RANKS = [
    {"order": 10, "name": "Station Officer Grade One / Group Officer Grade One", "abbreviation": "STN/OI / GO I"},
    {"order": 11, "name": "Station Officer Grade Two / Group Officer Grade Two", "abbreviation": "STN/OII / GO II"},
    {"order": 12, "name": "Assistant Station Officer / Deputy Group Officer", "abbreviation": "ASO / DGO"},
    {"order": 13, "name": "Subordinate Officer / Assistant Group Officer", "abbreviation": "SUB/O / AGO"},
    {"order": 14, "name": "Leading Fireman / Leading Firewoman", "abbreviation": "LFM / LFW"},
    {"order": 15, "name": "Senior Fireman / Senior Firewoman", "abbreviation": "SFM / SFW"},
    {"order": 16, "name": "Fireman / Firewoman", "abbreviation": "FM / FW"},
    {"order": 17, "name": "Recruit Fireman / Recruit Firewoman", "abbreviation": "RFM / RFW"},
]

# Comprehensive alias mapping for historical personnel text entries
RANK_ALIAS_MAP = {
    # Senior Officers
    "CFO": "Chief Fire Officer",
    "DCFO": "Deputy Chief Fire Officer",
    "ACFO": "Assistant Chief Fire Officer",
    "DOI": "Divisional Officer Grade One",
    "DO I": "Divisional Officer Grade One",
    "DOII": "Divisional Officer Grade Two",
    "DO II": "Divisional Officer Grade Two",
    "DOIII": "Divisional Officer Grade Three",
    "DO III": "Divisional Officer Grade Three",
    "ADOI": "Assistant Divisional Officer Grade One",
    "ADO I": "Assistant Divisional Officer Grade One",
    "ADOII": "Assistant Divisional Officer Grade Two",
    "ADO II": "Assistant Divisional Officer Grade Two",
    "CO": "Cadet Officer",

    # Junior Officers
    "STNOI": "Station Officer Grade One / Group Officer Grade One",
    "STN/OI": "Station Officer Grade One / Group Officer Grade One",
    "GOI": "Station Officer Grade One / Group Officer Grade One",
    "GO I": "Station Officer Grade One / Group Officer Grade One",
    "STN/OI / GO I": "Station Officer Grade One / Group Officer Grade One",

    "STNOII": "Station Officer Grade Two / Group Officer Grade Two",
    "STN/OII": "Station Officer Grade Two / Group Officer Grade Two",
    "GOII": "Station Officer Grade Two / Group Officer Grade Two",
    "GO II": "Station Officer Grade Two / Group Officer Grade Two",
    "STN/OII / GO II": "Station Officer Grade Two / Group Officer Grade Two",

    "ASTNO": "Assistant Station Officer / Deputy Group Officer",
    "ASO": "Assistant Station Officer / Deputy Group Officer",
    "DGO": "Assistant Station Officer / Deputy Group Officer",
    "ASO / DGO": "Assistant Station Officer / Deputy Group Officer",

    "SUB O": "Subordinate Officer / Assistant Group Officer",
    "SUB/O": "Subordinate Officer / Assistant Group Officer",
    "AGO": "Subordinate Officer / Assistant Group Officer",
    "SUB/O / AGO": "Subordinate Officer / Assistant Group Officer",

    "LFM": "Leading Fireman / Leading Firewoman",
    "LFW": "Leading Fireman / Leading Firewoman",
    "LFM / LFW": "Leading Fireman / Leading Firewoman",

    "SFM": "Senior Fireman / Senior Firewoman",
    "SFW": "Senior Fireman / Senior Firewoman",
    "SFM / SFW": "Senior Fireman / Senior Firewoman",

    "FM": "Fireman / Firewoman",
    "FW": "Fireman / Firewoman",
    "FWN": "Fireman / Firewoman",
    "FIREMAN": "Fireman / Firewoman",
    "FIREWOMAN": "Fireman / Firewoman",
    "FM / FW": "Fireman / Firewoman",

    "RFM": "Recruit Fireman / Recruit Firewoman",
    "RFW": "Recruit Fireman / Recruit Firewoman",
    "RECRUIT": "Recruit Fireman / Recruit Firewoman",
    "RFM / RFW": "Recruit Fireman / Recruit Firewoman",
}


class Command(BaseCommand):
    help = "Seed official GNFS Senior and Junior Officer ranks and safely migrate existing personnel."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-migration",
            action="store_true",
            help="Skip migrating existing personnel records to the new Rank model.",
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("GNFS RANK & JOB GRADE SEEDER"))
        self.stdout.write("------------------------------------")

        created_count = 0
        updated_count = 0
        unchanged_count = 0

        ranks_dict = {}

        with transaction.atomic():
            # 1. Seed Senior Officer Ranks
            for item in SENIOR_RANKS:
                rank_obj, created = Rank.objects.update_or_create(
                    name=item["name"],
                    defaults={
                        "abbreviation": item["abbreviation"],
                        "officer_category": Rank.OfficerCategory.SENIOR,
                        "hierarchy_order": item["order"],
                        "active": True,
                    }
                )
                ranks_dict[item["name"]] = rank_obj
                if created:
                    created_count += 1
                else:
                    # Check if fields were modified or unchanged
                    updated_count += 1

            # 2. Seed Junior Officer Ranks
            for item in JUNIOR_RANKS:
                rank_obj, created = Rank.objects.update_or_create(
                    name=item["name"],
                    defaults={
                        "abbreviation": item["abbreviation"],
                        "officer_category": Rank.OfficerCategory.JUNIOR,
                        "hierarchy_order": item["order"],
                        "active": True,
                    }
                )
                ranks_dict[item["name"]] = rank_obj
                if created:
                    created_count += 1
                else:
                    updated_count += 1

        senior_count = Rank.objects.filter(officer_category=Rank.OfficerCategory.SENIOR).count()
        junior_count = Rank.objects.filter(officer_category=Rank.OfficerCategory.JUNIOR).count()

        # If it was already seeded, mark as unchanged
        if created_count == 0 and updated_count > 0:
            unchanged_count = updated_count
            updated_count = 0

        self.stdout.write("\nGNFS Rank Seeder Complete\n")
        self.stdout.write(f"Senior Officer Ranks: {senior_count}")
        self.stdout.write(f"Junior Officer Ranks: {junior_count}\n")
        self.stdout.write(f"Created: {created_count}")
        self.stdout.write(f"Updated: {updated_count}")
        self.stdout.write(f"Unchanged: {unchanged_count}\n")

        # 3. Safe Data Migration for Existing Personnel
        if not options.get("no_migration"):
            self.stdout.write(self.style.MIGRATE_HEADING("PERSONNEL RANK DATA MIGRATION"))
            self.stdout.write("------------------------------------")

            total_staff = NorminalRoll.objects.count()
            matched_count = 0
            unmatched_staff = []

            with transaction.atomic():
                for staff in NorminalRoll.objects.all():
                    # If already linked to Rank, count as matched
                    if staff.rank:
                        matched_count += 1
                        continue

                    raw_grade = (staff.job_grade or "").strip().upper()
                    canonical_name = RANK_ALIAS_MAP.get(raw_grade)

                    if not canonical_name:
                        # Attempt matching directly against Rank names or abbreviations
                        for r_name, r_obj in ranks_dict.items():
                            if raw_grade == r_obj.abbreviation.upper() or raw_grade == r_obj.name.upper():
                                canonical_name = r_name
                                break

                    if canonical_name and canonical_name in ranks_dict:
                        target_rank = ranks_dict[canonical_name]
                        staff.rank = target_rank
                        staff.officer_category = target_rank.officer_category
                        staff.job_grade = target_rank.abbreviation
                        staff.save(update_fields=["rank", "officer_category", "job_grade"])
                        matched_count += 1
                    else:
                        unmatched_staff.append((staff.staffID, staff.fullname, staff.job_grade))

            self.stdout.write(f"Total personnel records processed: {total_staff}")
            self.stdout.write(self.style.SUCCESS(f"Successfully matched and migrated: {matched_count}"))

            if unmatched_staff:
                self.stdout.write(self.style.WARNING(f"Records requiring manual rank review: {len(unmatched_staff)}"))
                for s_id, s_name, s_grade in unmatched_staff:
                    self.stdout.write(f"  - StaffID: {s_id}, Name: {s_name}, Job Grade: {s_grade}")
            else:
                self.stdout.write(self.style.SUCCESS("All personnel records successfully linked to authoritative Ranks. Zero unmapped records.\n"))
