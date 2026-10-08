"""
Functional Organisational Office Service for GNFS.
Manages functional offices across National, Regional, and District levels:
- Administration
- Accounts
- Operations
- Human Resource (HR)
- Watch Room

Implements Fallback Responsibility (Section 2, 3, 37, 85, 86):
Where a dedicated functional office (e.g. HR in KEEA District) is not established,
responsibility falls back to the configured fallback office (e.g. District Administration).
"""

import logging
from django.db import transaction
from gnfs_komenda.models import FunctionalOffice, Region, District

logger = logging.getLogger(__name__)


def seed_all_functional_offices():
    """
    Seeds authoritative functional offices across National, Regional, and District commands.
    Ensures that every district has Administration, Accounts, Operations, Watch Room,
    and a Human Resource office (with fallback to District Administration where unestablished).
    """
    with transaction.atomic():
        # 1. National Directorates / Functional Offices
        nat_admin, _ = FunctionalOffice.objects.get_or_create(
            function_type=FunctionalOffice.FunctionType.ADMINISTRATION,
            command_level=FunctionalOffice.CommandLevel.NATIONAL,
            region=None,
            district=None,
            defaults={"name": "National Administration Directorate", "is_established": True, "active": True}
        )
        nat_accounts, _ = FunctionalOffice.objects.get_or_create(
            function_type=FunctionalOffice.FunctionType.ACCOUNTS,
            command_level=FunctionalOffice.CommandLevel.NATIONAL,
            region=None,
            district=None,
            defaults={"name": "National Accounts Directorate", "is_established": True, "active": True}
        )
        nat_ops, _ = FunctionalOffice.objects.get_or_create(
            function_type=FunctionalOffice.FunctionType.OPERATIONS,
            command_level=FunctionalOffice.CommandLevel.NATIONAL,
            region=None,
            district=None,
            defaults={"name": "National Operations Directorate", "is_established": True, "active": True}
        )
        nat_hr, _ = FunctionalOffice.objects.get_or_create(
            function_type=FunctionalOffice.FunctionType.HR,
            command_level=FunctionalOffice.CommandLevel.NATIONAL,
            region=None,
            district=None,
            defaults={"name": "National Human Resource Directorate", "is_established": True, "active": True}
        )

        national_offices = {
            FunctionalOffice.FunctionType.ADMINISTRATION: nat_admin,
            FunctionalOffice.FunctionType.ACCOUNTS: nat_accounts,
            FunctionalOffice.FunctionType.OPERATIONS: nat_ops,
            FunctionalOffice.FunctionType.HR: nat_hr,
        }

        # 2. Regional Functional Offices (For all active Regions)
        regional_offices = {}
        for region in Region.objects.filter(active=True):
            r_admin, _ = FunctionalOffice.objects.get_or_create(
                function_type=FunctionalOffice.FunctionType.ADMINISTRATION,
                command_level=FunctionalOffice.CommandLevel.REGIONAL,
                region=region,
                district=None,
                defaults={
                    "name": f"{region.name} Regional Administration",
                    "parent": nat_admin,
                    "is_established": True,
                    "active": True
                }
            )
            r_accounts, _ = FunctionalOffice.objects.get_or_create(
                function_type=FunctionalOffice.FunctionType.ACCOUNTS,
                command_level=FunctionalOffice.CommandLevel.REGIONAL,
                region=region,
                district=None,
                defaults={
                    "name": f"{region.name} Regional Accounts",
                    "parent": nat_accounts,
                    "is_established": True,
                    "active": True
                }
            )
            r_ops, _ = FunctionalOffice.objects.get_or_create(
                function_type=FunctionalOffice.FunctionType.OPERATIONS,
                command_level=FunctionalOffice.CommandLevel.REGIONAL,
                region=region,
                district=None,
                defaults={
                    "name": f"{region.name} Regional Operations",
                    "parent": nat_ops,
                    "is_established": True,
                    "active": True
                }
            )
            r_hr, _ = FunctionalOffice.objects.get_or_create(
                function_type=FunctionalOffice.FunctionType.HR,
                command_level=FunctionalOffice.CommandLevel.REGIONAL,
                region=region,
                district=None,
                defaults={
                    "name": f"{region.name} Regional Human Resource",
                    "parent": nat_hr,
                    "is_established": True,
                    "active": True
                }
            )
            regional_offices[region.id] = {
                FunctionalOffice.FunctionType.ADMINISTRATION: r_admin,
                FunctionalOffice.FunctionType.ACCOUNTS: r_accounts,
                FunctionalOffice.FunctionType.OPERATIONS: r_ops,
                FunctionalOffice.FunctionType.HR: r_hr,
            }

        # 3. District Functional Offices (For all active Districts)
        district_counts = 0
        for district in District.objects.filter(active=True).select_related('region'):
            reg_map = regional_offices.get(district.region_id, national_offices)

            # District Administration
            d_admin, _ = FunctionalOffice.objects.get_or_create(
                function_type=FunctionalOffice.FunctionType.ADMINISTRATION,
                command_level=FunctionalOffice.CommandLevel.DISTRICT,
                region=district.region,
                district=district,
                defaults={
                    "name": f"{district.name} Administration",
                    "parent": reg_map.get(FunctionalOffice.FunctionType.ADMINISTRATION),
                    "is_established": True,
                    "active": True
                }
            )

            # District Accounts
            d_accounts, _ = FunctionalOffice.objects.get_or_create(
                function_type=FunctionalOffice.FunctionType.ACCOUNTS,
                command_level=FunctionalOffice.CommandLevel.DISTRICT,
                region=district.region,
                district=district,
                defaults={
                    "name": f"{district.name} Accounts",
                    "parent": reg_map.get(FunctionalOffice.FunctionType.ACCOUNTS),
                    "is_established": True,
                    "active": True
                }
            )

            # District Operations
            d_ops, _ = FunctionalOffice.objects.get_or_create(
                function_type=FunctionalOffice.FunctionType.OPERATIONS,
                command_level=FunctionalOffice.CommandLevel.DISTRICT,
                region=district.region,
                district=district,
                defaults={
                    "name": f"{district.name} Operations",
                    "parent": reg_map.get(FunctionalOffice.FunctionType.OPERATIONS),
                    "is_established": True,
                    "active": True
                }
            )

            # District Watch Room (Section 40)
            d_watch, _ = FunctionalOffice.objects.get_or_create(
                function_type=FunctionalOffice.FunctionType.WATCH_ROOM,
                command_level=FunctionalOffice.CommandLevel.DISTRICT,
                region=district.region,
                district=district,
                defaults={
                    "name": f"{district.name} Watch Room",
                    "parent": d_ops,
                    "is_established": True,
                    "active": True
                }
            )

            # District HR (Section 2, 3, 37: KEEA and similar commands have NO dedicated HR; fallback to Administration)
            is_keea = "KEEA" in district.name.upper() or "KOMENDA" in district.name.upper()
            d_hr, created = FunctionalOffice.objects.get_or_create(
                function_type=FunctionalOffice.FunctionType.HR,
                command_level=FunctionalOffice.CommandLevel.DISTRICT,
                region=district.region,
                district=district,
                defaults={
                    "name": f"{district.name} Human Resource",
                    "parent": reg_map.get(FunctionalOffice.FunctionType.HR),
                    "is_established": not is_keea,
                    "fallback_office": d_admin,
                    "active": True
                }
            )
            if not created and is_keea and d_hr.is_established:
                d_hr.is_established = False
                d_hr.fallback_office = d_admin
                d_hr.save(update_fields=['is_established', 'fallback_office'])

            district_counts += 1

        total_offices = FunctionalOffice.objects.count()
        return {
            "total_offices": total_offices,
            "districts_processed": district_counts
        }
