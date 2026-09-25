"""
Seed Ghana Statutory Public Holidays for 2025, 2026, and 2027.
Permits dynamic configuration and addition of holidays via Admin or UI.
"""

from datetime import date
from django.core.management.base import BaseCommand
from gnfs_komenda.models import PublicHoliday


class Command(BaseCommand):
    help = "Seed Ghana Statutory Public Holidays for 2025-2027"

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("GHANA STATUTORY PUBLIC HOLIDAYS SEEDER"))

        holidays = [
            # 2025
            {"name": "New Year's Day", "date": date(2025, 1, 1), "year": 2025},
            {"name": "Constitution Day", "date": date(2025, 1, 7), "year": 2025},
            {"name": "Independence Day", "date": date(2025, 3, 6), "year": 2025},
            {"name": "Eid al-Fitr", "date": date(2025, 3, 31), "year": 2025},
            {"name": "Good Friday", "date": date(2025, 4, 18), "year": 2025},
            {"name": "Easter Monday", "date": date(2025, 4, 21), "year": 2025},
            {"name": "Workers' Day / May Day", "date": date(2025, 5, 1), "year": 2025},
            {"name": "AU Day / Africa Day", "date": date(2025, 5, 25), "year": 2025},
            {"name": "Eid al-Adha", "date": date(2025, 6, 7), "year": 2025},
            {"name": "Founders' Day", "date": date(2025, 8, 4), "year": 2025},
            {"name": "Kwame Nkrumah Memorial Day", "date": date(2025, 9, 21), "year": 2025},
            {"name": "Farmers' Day", "date": date(2025, 12, 5), "year": 2025},
            {"name": "Christmas Day", "date": date(2025, 12, 25), "year": 2025},
            {"name": "Boxing Day", "date": date(2025, 12, 26), "year": 2025},

            # 2026
            {"name": "New Year's Day", "date": date(2026, 1, 1), "year": 2026},
            {"name": "Constitution Day", "date": date(2026, 1, 7), "year": 2026},
            {"name": "Independence Day", "date": date(2026, 3, 6), "year": 2026},
            {"name": "Eid al-Fitr", "date": date(2026, 3, 20), "year": 2026},
            {"name": "Good Friday", "date": date(2026, 4, 3), "year": 2026},
            {"name": "Easter Monday", "date": date(2026, 4, 6), "year": 2026},
            {"name": "Workers' Day / May Day", "date": date(2026, 5, 1), "year": 2026},
            {"name": "AU Day / Africa Day", "date": date(2026, 5, 25), "year": 2026},
            {"name": "Eid al-Adha", "date": date(2026, 5, 27), "year": 2026},
            {"name": "Founders' Day", "date": date(2026, 8, 4), "year": 2026},
            {"name": "Kwame Nkrumah Memorial Day", "date": date(2026, 9, 21), "year": 2026},
            {"name": "Farmers' Day", "date": date(2026, 12, 4), "year": 2026},
            {"name": "Christmas Day", "date": date(2026, 12, 25), "year": 2026},
            {"name": "Boxing Day", "date": date(2026, 12, 26), "year": 2026},

            # 2027
            {"name": "New Year's Day", "date": date(2027, 1, 1), "year": 2027},
            {"name": "Constitution Day", "date": date(2027, 1, 7), "year": 2027},
            {"name": "Independence Day", "date": date(2027, 3, 6), "year": 2027},
            {"name": "Eid al-Fitr", "date": date(2027, 3, 10), "year": 2027},
            {"name": "Good Friday", "date": date(2027, 3, 26), "year": 2027},
            {"name": "Easter Monday", "date": date(2027, 3, 29), "year": 2027},
            {"name": "Workers' Day / May Day", "date": date(2027, 5, 1), "year": 2027},
            {"name": "AU Day / Africa Day", "date": date(2027, 5, 25), "year": 2027},
            {"name": "Eid al-Adha", "date": date(2027, 5, 17), "year": 2027},
            {"name": "Founders' Day", "date": date(2027, 8, 4), "year": 2027},
            {"name": "Kwame Nkrumah Memorial Day", "date": date(2027, 9, 21), "year": 2027},
            {"name": "Farmers' Day", "date": date(2027, 12, 3), "year": 2027},
            {"name": "Christmas Day", "date": date(2027, 12, 25), "year": 2027},
            {"name": "Boxing Day", "date": date(2027, 12, 26), "year": 2027},
        ]

        created_count = 0
        updated_count = 0

        for h in holidays:
            obj, created = PublicHoliday.objects.update_or_create(
                date=h["date"],
                defaults={
                    "name": h["name"],
                    "year": h["year"],
                    "active": True
                }
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded Public Holidays. Created: {created_count}, Updated: {updated_count}, Total in DB: {PublicHoliday.objects.count()}."
            )
        )
