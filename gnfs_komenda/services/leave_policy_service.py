"""
GNFS Central Leave Policy Service
Authoritative business logic for core and custom leave policies,
entitlements, and eligibility.
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from gnfs_komenda.models import LeaveType, LeavePolicy, Rank, NorminalRoll


def seed_core_leave_policies():
    """
    Seed protected national core leave policies into LeavePolicy model.
    Guarantees:
    - Annual Senior: 42 working days (CORE)
    - Annual Junior: 36 working days (CORE)
    - Paternity: 5 working days, Male (CORE)
    - Maternity: 90 calendar days, Female (CORE)
    """
    policies_data = [
        {
            "policy_code": "ANNUAL_SENIOR",
            "leave_type_code": "ANNUAL",
            "name": "Annual Leave — Senior Officers",
            "eligible_sex": LeavePolicy.EligibleSex.ALL,
            "officer_category": Rank.OfficerCategory.SENIOR,
            "duration": 42,
            "calculation_mode": LeavePolicy.CalculationMode.WORKING_DAYS,
            "requires_document": False,
            "is_core_policy": True,
            "description": "Statutory annual leave for Senior Officers (42 working days). Excludes Saturdays, Sundays, and configured public holidays."
        },
        {
            "policy_code": "ANNUAL_JUNIOR",
            "leave_type_code": "ANNUAL",
            "name": "Annual Leave — Junior Officers",
            "eligible_sex": LeavePolicy.EligibleSex.ALL,
            "officer_category": Rank.OfficerCategory.JUNIOR,
            "duration": 36,
            "calculation_mode": LeavePolicy.CalculationMode.WORKING_DAYS,
            "requires_document": False,
            "is_core_policy": True,
            "description": "Statutory annual leave for Junior Officers (36 working days). Excludes Saturdays, Sundays, and configured public holidays."
        },
        {
            "policy_code": "PATERNITY",
            "leave_type_code": "PATERNITY",
            "name": "Paternity Leave",
            "eligible_sex": LeavePolicy.EligibleSex.MALE,
            "officer_category": "",
            "duration": 5,
            "calculation_mode": LeavePolicy.CalculationMode.WORKING_DAYS,
            "requires_document": True,
            "is_core_policy": True,
            "description": "Statutory paternity leave for male officers (5 working days). Excludes Saturdays, Sundays, and configured public holidays."
        },
        {
            "policy_code": "MATERNITY",
            "leave_type_code": "MATERNITY",
            "name": "Maternity Leave",
            "eligible_sex": LeavePolicy.EligibleSex.FEMALE,
            "officer_category": "",
            "duration": 90,
            "calculation_mode": LeavePolicy.CalculationMode.CALENDAR_DAYS,
            "requires_document": True,
            "is_core_policy": True,
            "description": "Statutory maternity leave for female officers (90 calendar days). Includes weekends and public holidays."
        },
        {
            "policy_code": "CASUAL",
            "leave_type_code": "CASUAL",
            "name": "Casual Leave",
            "eligible_sex": LeavePolicy.EligibleSex.ALL,
            "officer_category": "",
            "duration": 12,
            "calculation_mode": LeavePolicy.CalculationMode.WORKING_DAYS,
            "requires_document": False,
            "is_core_policy": False,
            "description": "Short-term urgent personal leave up to 12 working days per year."
        },
        {
            "policy_code": "SICK",
            "leave_type_code": "SICK",
            "name": "Sick Leave",
            "eligible_sex": LeavePolicy.EligibleSex.ALL,
            "officer_category": "",
            "duration": 0,
            "calculation_mode": LeavePolicy.CalculationMode.DATE_RANGE,
            "requires_document": True,
            "is_core_policy": False,
            "description": "Medical excuse-duty leave supported by verified medical certificate for specific date range."
        },
        {
            "policy_code": "TERMINAL",
            "leave_type_code": "TERMINAL",
            "name": "Terminal Leave",
            "eligible_sex": LeavePolicy.EligibleSex.ALL,
            "officer_category": "",
            "duration": 6,
            "calculation_mode": LeavePolicy.CalculationMode.CALENDAR_MONTHS,
            "requires_document": False,
            "is_core_policy": False,
            "description": "Pre-retirement statutory terminal leave (6 calendar months)."
        },
    ]

    with transaction.atomic():
        for pdata in policies_data:
            lt = LeaveType.objects.filter(code=pdata["leave_type_code"]).first()
            if not lt:
                continue
            LeavePolicy.objects.update_or_create(
                policy_code=pdata["policy_code"],
                defaults={
                    "leave_type": lt,
                    "name": pdata["name"],
                    "eligible_sex": pdata["eligible_sex"],
                    "officer_category": pdata["officer_category"],
                    "duration": pdata["duration"],
                    "calculation_mode": pdata["calculation_mode"],
                    "requires_document": pdata["requires_document"],
                    "is_core_policy": pdata["is_core_policy"],
                    "is_active": True,
                    "description": pdata["description"],
                }
            )


def get_base_annual_entitlement(officer_category):
    """
    Derive annual leave entitlement from configured policy or category fallback.
    Senior Officer = 42 working days
    Junior Officer = 36 working days
    """
    if officer_category == Rank.OfficerCategory.SENIOR:
        pol = LeavePolicy.objects.filter(policy_code="ANNUAL_SENIOR", is_active=True).first()
        return pol.duration if pol else 42
    pol = LeavePolicy.objects.filter(policy_code="ANNUAL_JUNIOR", is_active=True).first()
    return pol.duration if pol else 36


def calculate_annual_leave_entitlement(personnel, year=None):
    """
    Get or create the annual leave entitlement record for the given personnel and year,
    applying authoritative GNFS statutory rules (Senior: 42 working days, Junior: 36 working days).
    """
    from django.utils import timezone
    from gnfs_komenda.models import LeaveType, LeaveEntitlement
    if year is None:
        year = timezone.now().date().year
    lt_annual = LeaveType.objects.filter(code="ANNUAL").first() or LeaveType.objects.filter(name__icontains="Annual").first()
    base_annual = get_base_annual_entitlement(personnel.officer_category)
    entitlement, _ = LeaveEntitlement.objects.get_or_create(
        personnel=personnel,
        leave_year=year,
        leave_type=lt_annual,
        defaults={
            'base_entitlement': base_annual,
            'full_annual_entitlement': base_annual,
        }
    )
    return entitlement


def get_eligible_leave_types_for_personnel(personnel):
    """
    Return queryset of LeaveTypes that this officer is eligible for based on their Sex/Gender.
    - MALE: sees Annual, Paternity, Sick, Casual, Terminal, etc. (Excludes Maternity)
    - FEMALE: sees Annual, Maternity, Sick, Casual, Terminal, etc. (Excludes Paternity)
    - NOT_SET / Missing: Excludes BOTH Maternity and Paternity until sex is administratively completed.
    """
    qs = LeaveType.objects.filter(active=True).order_by('name')
    if not personnel:
        return qs.filter(allowed_gender=LeaveType.AllowedGender.ALL)

    gender = getattr(personnel, 'gender', None)

    if gender == NorminalRoll.Gender.MALE:
        return qs.exclude(allowed_gender=LeaveType.AllowedGender.FEMALE)
    elif gender == NorminalRoll.Gender.FEMALE:
        return qs.exclude(allowed_gender=LeaveType.AllowedGender.MALE)
    else:
        # Sex is NOT SET or missing: exclude both sex-restricted leave types
        return qs.exclude(allowed_gender__in=[
            LeaveType.AllowedGender.MALE,
            LeaveType.AllowedGender.FEMALE,
        ])


def validate_leave_eligibility_backend(personnel, leave_type, duration=None):
    """
    Authoritative backend validation of personnel leave eligibility.
    Rejects:
    - Male + Maternity
    - Female + Paternity
    - Missing Sex + Maternity/Paternity
    - Overriding fixed durations for Paternity (5) or Maternity (90)
    Returns:
        tuple (effective_duration, effective_calc_mode)
    """
    gender = getattr(personnel, 'gender', None)

    # 1. Paternity validation
    if leave_type.code == "PATERNITY" or leave_type.allowed_gender == LeaveType.AllowedGender.MALE:
        if gender != NorminalRoll.Gender.MALE:
            if gender == NorminalRoll.Gender.FEMALE:
                raise ValidationError("Paternity leave is strictly restricted to male personnel.")
            raise ValidationError(
                "Your personnel record requires a Sex value before Maternity/Paternity eligibility can be determined. "
                "Please contact an authorised administrator."
            )
        return 5, LeaveType.CalculationMode.WORKING_DAYS

    # 2. Maternity validation
    if leave_type.code == "MATERNITY" or leave_type.allowed_gender == LeaveType.AllowedGender.FEMALE:
        if gender != NorminalRoll.Gender.FEMALE:
            if gender == NorminalRoll.Gender.MALE:
                raise ValidationError("Maternity leave is strictly restricted to female personnel.")
            raise ValidationError(
                "Your personnel record requires a Sex value before Maternity/Paternity eligibility can be determined. "
                "Please contact an authorised administrator."
            )
        return 90, LeaveType.CalculationMode.CALENDAR_DAYS

    # 3. Annual Leave
    if leave_type.code == "ANNUAL":
        base_days = get_base_annual_entitlement(personnel.officer_category)
        return duration or base_days, LeaveType.CalculationMode.WORKING_DAYS

    return duration or leave_type.default_days_junior, leave_type.calculation_mode


def can_manage_proportionate_entitlement(user, target_personnel):
    """
    Check if user is authorized to adjust/prorate target_personnel's leave entitlement.
    - Superusers: nationwide
    - National Admin: nationwide
    - Regional Admin: within their region
    - District Admin: within their district
    - Ordinary personnel: CANNOT modify entitlement
    - Anti-self-adjustment: Admin cannot prorate their own entitlement
    """
    if not user or not user.is_authenticated or not target_personnel:
        return False

    actor_assignment = getattr(user, 'assignment', None)
    if actor_assignment and actor_assignment.personnel_id == target_personnel.id:
        return False

    if user.is_superuser:
        return True

    from gnfs_komenda.services.role_service import get_actor_administrative_scope
    scope = get_actor_administrative_scope(user)

    if scope["level"] == "NATIONAL":
        return True
    elif scope["level"] == "REGIONAL":
        reg = scope["region"]
        if reg:
            if target_personnel.station and target_personnel.station.district and target_personnel.station.district.region_id == reg.id:
                return True
            from gnfs_komenda.models import PersonnelPosting
            if PersonnelPosting.objects.filter(personnel=target_personnel, is_current=True, region=reg).exists():
                return True
    elif scope["level"] == "DISTRICT":
        dist = scope["district"]
        if dist:
            if target_personnel.station and target_personnel.station.district_id == dist.id:
                return True
            from gnfs_komenda.models import PersonnelPosting
            if PersonnelPosting.objects.filter(personnel=target_personnel, is_current=True, district=dist).exists():
                return True

    return False


def set_proportionate_annual_entitlement(entitlement, proportionate_days, reason, actor, effective_date=None):
    """
    Adjust an officer's annual leave entitlement to an approved proportionate / prorated value.
    - Validates proportionate days > 0 and <= normal entitlement.
    - Validates reason is provided.
    - Updates LeaveEntitlement.
    - Audited in LeaveAdjustmentAudit and AuditLog.
    """
    if not reason or not str(reason).strip():
        raise ValidationError("Reason is required when configuring proportionate leave entitlement.")

    try:
        proportionate_days = int(proportionate_days)
    except (ValueError, TypeError):
        raise ValidationError("Proportionate days must be a valid number.")

    if proportionate_days <= 0:
        raise ValidationError("Proportionate days must be greater than zero.")

    standard_days = entitlement.full_annual_entitlement or get_base_annual_entitlement(entitlement.personnel.officer_category)
    if proportionate_days > standard_days:
        raise ValidationError(f"Proportionate days ({proportionate_days}) cannot exceed the standard entitlement of {standard_days} working days.")

    from gnfs_komenda.models import AuditLog, LeaveAdjustmentAudit
    with transaction.atomic():
        if entitlement.full_annual_entitlement == 0:
            entitlement.full_annual_entitlement = standard_days

        prev_base = entitlement.base_entitlement
        prev_balance = entitlement.remaining_balance
        diff = proportionate_days - prev_base

        entitlement.base_entitlement = proportionate_days
        entitlement.is_prorated = True
        entitlement.proration_reason = str(reason).strip()
        entitlement.save(update_fields=['base_entitlement', 'is_prorated', 'proration_reason', 'full_annual_entitlement', 'updated_at'])

        LeaveAdjustmentAudit.objects.create(
            entitlement=entitlement,
            days_adjusted=diff,
            previous_balance=prev_balance,
            new_balance=entitlement.remaining_balance,
            reason=f"Proportionate entitlement: {reason}",
            adjusted_by=actor
        )
        AuditLog.objects.create(
            user=actor,
            action="ENTITLEMENT_PRORATED",
            target_type="LEAVE_ENTITLEMENT",
            target_id=str(entitlement.personnel.id),
            description=f"Prorated annual leave for {entitlement.personnel.fullname} ({entitlement.personnel.staffID}) to {proportionate_days} days. Standard: {standard_days}d. Reason: {reason}",
            reason=str(reason)
        )
        return entitlement


def restore_standard_annual_entitlement(entitlement, reason, actor):
    """
    Restore an officer's annual leave entitlement back to standard full-year entitlement.
    """
    standard_days = entitlement.full_annual_entitlement or get_base_annual_entitlement(entitlement.personnel.officer_category)
    from gnfs_komenda.models import AuditLog, LeaveAdjustmentAudit
    with transaction.atomic():
        prev_base = entitlement.base_entitlement
        prev_balance = entitlement.remaining_balance
        diff = standard_days - prev_base

        entitlement.base_entitlement = standard_days
        entitlement.is_prorated = False
        entitlement.proration_reason = ""
        entitlement.save(update_fields=['base_entitlement', 'is_prorated', 'proration_reason', 'full_annual_entitlement', 'updated_at'])

        LeaveAdjustmentAudit.objects.create(
            entitlement=entitlement,
            days_adjusted=diff,
            previous_balance=prev_balance,
            new_balance=entitlement.remaining_balance,
            reason=f"Restored standard entitlement: {reason}",
            adjusted_by=actor
        )
        AuditLog.objects.create(
            user=actor,
            action="ENTITLEMENT_RESTORED",
            target_type="LEAVE_ENTITLEMENT",
            target_id=str(entitlement.personnel.id),
            description=f"Restored standard annual leave for {entitlement.personnel.fullname} ({entitlement.personnel.staffID}) to {standard_days} days. Reason: {reason}",
            reason=str(reason)
        )
        return entitlement

