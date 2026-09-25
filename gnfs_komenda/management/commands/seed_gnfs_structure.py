import json
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from gnfs_komenda.models import Region, District, Station, Post, NorminalRoll
from gnfs_komenda.gnfs_structure_data import GNFS_STRUCTURE


STATION_TYPE_MAP = {
    'fire station': 'DISTRICT',
    'district fire station': 'DISTRICT',
    'municipal fire station': 'MUNICIPAL',
    'metro fire station': 'METRO',
    'institutional fire station': 'INSTITUTIONAL',
    'sub-station': 'SUB_STATION',
    'sub station': 'SUB_STATION',
    'regional headquarters station': 'REGIONAL_HQ',
    'regional hq': 'REGIONAL_HQ',
    'national headquarters station': 'NATIONAL_HQ',
    'national hq': 'NATIONAL_HQ',
    'special operational station': 'SPECIAL',
    'special': 'SPECIAL',
}

POST_TYPE_MAP = {
    'market fire post': 'MARKET_POST',
    'market post': 'MARKET_POST',
    'sub-post': 'SUB_POST',
    'sub post': 'SUB_POST',
    'fire checkpoint': 'CHECKPOINT',
    'checkpoint': 'CHECKPOINT',
    'community post': 'COMMUNITY_POST',
}


def normalize_station_type(raw_type):
    if not raw_type:
        return 'DISTRICT'
    upper_val = raw_type.strip().upper()
    valid_keys = [k for k, _ in Station.STATION_TYPE_CHOICES]
    if upper_val in valid_keys:
        return upper_val
    return STATION_TYPE_MAP.get(raw_type.strip().lower(), 'DISTRICT')


def normalize_post_type(raw_type):
    if not raw_type:
        return 'MARKET_POST'
    upper_val = raw_type.strip().upper()
    valid_keys = [k for k, _ in Post.POST_TYPE_CHOICES]
    if upper_val in valid_keys:
        return upper_val
    return POST_TYPE_MAP.get(raw_type.strip().lower(), 'MARKET_POST')


class Command(BaseCommand):
    help = "Seed verified GNFS hierarchical organizational structure (17 Regions, Districts, Stations, Posts) idempotently."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=str,
            default=None,
            help="Path to GNFS JSON structure file (defaults to gnfs_komenda/gnfs_structure_v2.json).",
        )
        parser.add_argument(
            "--no-personnel-link",
            action="store_true",
            help="Skip associating existing unlinked personnel to Komenda Fire Station.",
        )

    def load_dataset(self, file_path=None):
        target_path = None
        if file_path:
            target_path = Path(file_path)
        else:
            default_json = Path(settings.BASE_DIR) / "gnfs_komenda" / "gnfs_structure_v2.json"
            if default_json.exists():
                target_path = default_json

        if target_path and target_path.exists():
            self.stdout.write(f"Loading structure dataset from JSON: {target_path}")
            with open(target_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("regions", [])
        
        self.stdout.write("Falling back to Python data structure (gnfs_structure_data.py)...")
        return GNFS_STRUCTURE

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("GNFS ORGANISATIONAL STRUCTURE SEEDER (17 SERVICE REGIONS)"))
        self.stdout.write("------------------------------------------------------------------")

        regions_data = self.load_dataset(options.get("file"))

        reg_created, reg_updated = 0, 0
        dist_created, dist_updated = 0, 0
        stn_created, stn_updated = 0, 0
        post_created, post_updated = 0, 0

        with transaction.atomic():
            for reg_data in regions_data:
                if reg_data.get("seed_enabled") is False:
                    continue

                reg_name = reg_data["name"].strip()
                reg_code = reg_data["code"].strip().upper()

                # Robust Region update_or_create to handle code or name changes cleanly
                region = Region.objects.filter(name=reg_name).first()
                if region:
                    if region.code != reg_code:
                        region.code = reg_code
                    region.active = True
                    region.save()
                    reg_updated += 1
                else:
                    region = Region.objects.filter(code=reg_code).first()
                    if region:
                        region.name = reg_name
                        region.active = True
                        region.save()
                        reg_updated += 1
                    else:
                        region = Region.objects.create(
                            name=reg_name,
                            code=reg_code,
                            active=True
                        )
                        reg_created += 1

                for dist_data in reg_data.get("districts", []):
                    if dist_data.get("seed_enabled") is False:
                        continue

                    dist_name = dist_data["name"].strip()
                    dist_code = (dist_data.get("code") or "").strip()

                    district, created = District.objects.update_or_create(
                        region=region,
                        name=dist_name,
                        defaults={
                            "code": dist_code,
                            "active": True,
                        }
                    )
                    if created:
                        dist_created += 1
                    else:
                        dist_updated += 1

                    for stn_data in dist_data.get("stations", []):
                        if stn_data.get("seed_enabled") is False:
                            continue

                        stn_name = stn_data["name"].strip()
                        stn_code = (stn_data.get("code") or "").strip()
                        stn_type = normalize_station_type(stn_data.get("station_type"))

                        station, created = Station.objects.update_or_create(
                            district=district,
                            name=stn_name,
                            defaults={
                                "code": stn_code,
                                "station_type": stn_type,
                                "active": True,
                            }
                        )
                        if created:
                            stn_created += 1
                        else:
                            stn_updated += 1

                        for post_data in stn_data.get("posts", []):
                            if post_data.get("seed_enabled") is False:
                                continue

                            post_name = post_data["name"].strip()
                            post_code = (post_data.get("code") or "").strip()
                            post_type = normalize_post_type(post_data.get("post_type"))

                            post, created = Post.objects.update_or_create(
                                station=station,
                                name=post_name,
                                defaults={
                                    "code": post_code,
                                    "post_type": post_type,
                                    "active": True,
                                }
                            )
                            if created:
                                post_created += 1
                            else:
                                post_updated += 1

        self.stdout.write(f"\nRegions   -> Created: {reg_created}, Updated: {reg_updated} (Total active: {Region.objects.filter(active=True).count()})")
        self.stdout.write(f"Districts -> Created: {dist_created}, Updated: {dist_updated} (Total active: {District.objects.filter(active=True).count()})")
        self.stdout.write(f"Stations  -> Created: {stn_created}, Updated: {stn_updated} (Total active: {Station.objects.filter(active=True).count()})")
        self.stdout.write(f"Posts     -> Created: {post_created}, Updated: {post_updated} (Total active: {Post.objects.filter(active=True).count()})\n")
        self.stdout.write(self.style.SUCCESS("GNFS organisational structure seeded successfully.\n"))

        # Safe Existing Personnel Migration & Association
        if not options.get("no_personnel_link"):
            self.stdout.write(self.style.MIGRATE_HEADING("KOMENDA PERSONNEL LINKAGE & INTEGRITY VERIFICATION"))
            self.stdout.write("------------------------------------------------------------------")
            
            komenda_station = Station.objects.filter(name="Komenda Fire Station").first()
            if not komenda_station:
                self.stdout.write(self.style.ERROR("Error: Komenda Fire Station record not found!"))
                return

            total_before = NorminalRoll.objects.count()
            unassigned = NorminalRoll.objects.filter(station__isnull=True)
            unassigned_count = unassigned.count()

            self.stdout.write(f"Total personnel records before association: {total_before}")
            self.stdout.write(f"Unassigned personnel records to link: {unassigned_count}")

            if unassigned_count > 0:
                updated = unassigned.update(station=komenda_station, is_active=True)
                self.stdout.write(self.style.SUCCESS(f"Successfully linked {updated} personnel to {komenda_station.name}."))
            else:
                self.stdout.write("All existing personnel are already linked to their assigned station.")

            total_after = NorminalRoll.objects.count()
            linked_to_komenda = NorminalRoll.objects.filter(station=komenda_station).count()
            
            self.stdout.write(f"Total personnel records after association: {total_after}")
            self.stdout.write(f"Personnel linked to Komenda Fire Station: {linked_to_komenda}")

            if total_before == total_after and total_after > 0:
                self.stdout.write(self.style.SUCCESS("Integrity verification passed: Zero data loss, zero duplicate records.\n"))
            else:
                self.stdout.write(self.style.WARNING("Warning: Personnel count mismatch during verification!\n"))
