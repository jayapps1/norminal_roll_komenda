"""
Seed GNFS Leave Types, initialize 2026 LeaveEntitlements for all personnel,
and migrate legacy leave records into authoritative LeaveRequest instances.
"""

from datetime import date
from django.core.management.base import BaseCommand
from django.utils import timezone

from gnfs_komenda.models import (
    LeaveType,
    LeaveEntitlement,
    LeaveRequest,
    NorminalRoll
)
from gnfs_komenda.services.leave_calculator import calculate_leave_dates


class Command(BaseCommand):
    help = "Seed authoritative GNFS Leave Types, initialize entitlements, and migrate legacy leave records."

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("GNFS LEAVE TYPES SEEDER"))

        leave_types_data = [
            {
                "code": "ANNUAL",
                "name": "Annual Leave",
                "calculation_mode": LeaveType.CalculationMode.WORKING_DAYS,
                "allowed_gender": LeaveType.AllowedGender.ALL,
                "default_days_junior": 36,
                "default_days_senior": 42,
                "calendar_months": 0,
                "is_deferrable": True,
                "requires_attachment": False,
                "description": "Statutory annual leave (36 working days for Junior Officers, 42 working days for Senior Officers). Excludes weekends and public holidays."
            },
            {
                "code": "CASUAL",
                "name": "Casual Leave",
                "calculation_mode": LeaveType.CalculationMode.WORKING_DAYS,
                "allowed_gender": LeaveType.AllowedGender.ALL,
                "default_days_junior": 12,
                "default_days_senior": 12,
                "calendar_months": 0,
                "is_deferrable": False,
                "requires_attachment": False,
                "description": "Short-term urgent personal leave up to 12 working days per year."
            },
            {
                "code": "MATERNITY",
                "name": "Maternity Leave",
                "calculation_mode": LeaveType.CalculationMode.CALENDAR_DAYS,
                "allowed_gender": LeaveType.AllowedGender.FEMALE,
                "default_days_junior": 90,
                "default_days_senior": 90,
                "calendar_months": 0,
                "is_deferrable": False,
                "requires_attachment": True,
                "description": "Statutory maternity leave for female personnel (90 calendar days)."
            },
            {
                "code": "PATERNITY",
                "name": "Paternity Leave",
                "calculation_mode": LeaveType.CalculationMode.WORKING_DAYS,
                "allowed_gender": LeaveType.AllowedGender.MALE,
                "default_days_junior": 5,
                "default_days_senior": 5,
                "calendar_months": 0,
                "is_deferrable": False,
                "requires_attachment": True,
                "description": "Statutory paternity leave for male personnel (5 working days). Excludes weekends and public holidays."
            },
            {
                "code": "SICK",
                "name": "Sick Leave",
                "calculation_mode": LeaveType.CalculationMode.DATE_RANGE,
                "allowed_gender": LeaveType.AllowedGender.ALL,
                "default_days_junior": 0,
                "default_days_senior": 0,
                "calendar_months": 0,
                "is_deferrable": False,
                "requires_attachment": True,
                "description": "Medical leave supported by qualified medical certificates for specified date range."
            },
            {
                "code": "TERMINAL",
                "name": "Terminal Leave",
                "calculation_mode": LeaveType.CalculationMode.CALENDAR_MONTHS,
                "allowed_gender": LeaveType.AllowedGender.ALL,
                "default_days_junior": 0,
                "default_days_senior": 0,
                "calendar_months": 6,
                "is_deferrable": False,
                "requires_attachment": False,
                "description": "Pre-retirement terminal leave (6 calendar months)."
            },
            {
                "code": "STUDY",
                "name": "Study Leave",
                "calculation_mode": LeaveType.CalculationMode.DATE_RANGE,
                "allowed_gender": LeaveType.AllowedGender.ALL,
                "default_days_junior": 0,
                "default_days_senior": 0,
                "calendar_months": 0,
                "is_deferrable": False,
                "requires_attachment": True,
                "description": "Approved academic or professional study leave."
            },
        ]

        types_created = 0
        types_updated = 0
        for data in leave_types_data:
            code = data.pop("code")
            obj, created = LeaveType.objects.update_or_create(
                code=code,
                defaults=data
            )
            if created:
                types_created += 1
            else:
                types_updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Leave Types configured. Created: {types_created}, Updated: {types_updated}."
            )
        )

        # 2. Initialize 2026 Entitlements for all existing personnel
        self.stdout.write(self.style.MIGRATE_HEADING("INITIALIZING 2026 PERSONNEL ENTITLEMENTS"))
        annual_type = LeaveType.objects.get(code="ANNUAL")
        casual_type = LeaveType.objects.get(code="CASUAL")

        personnel_qs = NorminalRoll.objects.all()
        entitlements_created = 0
        entitlements_updated = 0

        current_year = 2026

        for p in personnel_qs:
            # Determine annual leave entitlement based on category
            is_senior = p.officer_category == "SENIOR"
            annual_days = 42 if is_senior else 36

            # Annual leave entitlement
            ent_annual, created_annual = LeaveEntitlement.objects.get_or_create(
                personnel=p,
                leave_year=current_year,
                leave_type=annual_type,
                defaults={"base_entitlement": annual_days}
            )
            if created_annual:
                entitlements_created += 1
            else:
                if ent_annual.base_entitlement != annual_days:
                    ent_annual.base_entitlement = annual_days
                    ent_annual.save(update_fields=["base_entitlement"])
                    entitlements_updated += 1

            # Casual leave entitlement (12 days)
            ent_casual, created_casual = LeaveEntitlement.objects.get_or_create(
                personnel=p,
                leave_year=current_year,
                leave_type=casual_type,
                defaults={"base_entitlement": 12}
            )
            if created_casual:
                entitlements_created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Entitlements initialized for {personnel_qs.count()} officers. "
                f"Created: {entitlements_created}, Updated: {entitlements_updated}."
            )
        )

        # 3. Migrate Legacy Leave Records
        self.stdout.write(self.style.MIGRATE_HEADING("MIGRATING HISTORICAL LEAVE RECORDS"))
        today = timezone.localdate()

        legacy_leaves = NorminalRoll.objects.filter(
            start_leave__isnull=False,
            end_leave__isnull=False
        )
        migrated_leaves = 0

        # Type mapping dictionary to handle variations like "THERMINAL LEAVE"
        def map_legacy_type(raw_type):
            if not raw_type:
                return annual_type
            raw_upper = str(raw_type).strip().upper()
            if "CASUAL" in raw_upper:
                return casual_type
            elif "MATERNITY" in raw_upper:
                return LeaveType.objects.get(code="MATERNITY")
            elif "SICK" in raw_upper:
                return LeaveType.objects.get(code="SICK")
            elif "TERMINAL" in raw_upper or "THERMINAL" in raw_upper:
                return LeaveType.objects.get(code="TERMINAL")
            elif "STUDY" in raw_upper:
                return LeaveType.objects.get(code="STUDY")
            return annual_type

        for p in legacy_leaves:
            l_type = map_legacy_type(p.leave_type)

            # Check if an existing LeaveRequest already covers this exact period
            existing = LeaveRequest.objects.filter(
                personnel=p,
                start_date=p.start_leave,
                last_leave_date=p.end_leave
            ).first()

            if not existing:
                try:
                    # Calculate duration and days
                    calc = calculate_leave_dates(
                        start_date=p.start_leave,
                        duration=(p.end_leave - p.start_leave).days + 1,
                        calculation_mode=LeaveType.CalculationMode.DATE_RANGE,
                        end_date=p.end_leave
                    )
                    working_days = calc['working_days_count']
                    calendar_days = calc['calendar_days_count']
                    resume_date = calc['resume_date']
                except Exception:
                    working_days = (p.end_leave - p.start_leave).days + 1
                    calendar_days = working_days
                    resume_date = p.end_leave

                # Determine status
                if p.called_off:
                    status = LeaveRequest.Status.RECALLED
                elif p.end_leave < today:
                    status = LeaveRequest.Status.COMPLETED
                elif p.start_leave <= today <= p.end_leave and p.leave_status == "ON LEAVE":
                    status = LeaveRequest.Status.ACTIVE
                else:
                    status = LeaveRequest.Status.APPROVED

                # Find matching entitlement if applicable
                leave_year = p.start_leave.year
                entitlement = LeaveEntitlement.objects.filter(
                    personnel=p,
                    leave_year=leave_year,
                    leave_type=l_type
                ).first()

                LeaveRequest.objects.create(
                    personnel=p,
                    entitlement=entitlement,
                    leave_type=l_type,
                    calculation_mode=l_type.calculation_mode,
                    start_date=p.start_leave,
                    duration=working_days if l_type.calculation_mode == LeaveType.CalculationMode.WORKING_DAYS else calendar_days,
                    last_leave_date=p.end_leave,
                    resume_date=resume_date,
                    working_days_count=working_days,
                    calendar_days_count=calendar_days,
                    status=status,
                    is_recalled=p.called_off,
                    reason="Migrated historical leave record"
                )
                migrated_leaves += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Historical leave migration complete. Migrated: {migrated_leaves} records."
            )
        )
