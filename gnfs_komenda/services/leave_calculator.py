"""
Leave Calculation Service for Ghana National Fire Service (GNFS).
Authoritative date and duration engine supporting:
- WORKING_DAYS (excludes Saturdays, Sundays, and active PublicHoliday records)
- CALENDAR_DAYS (consecutive days)
- DATE_RANGE (custom start to end dates)
- CALENDAR_MONTHS (exact calendar month arithmetic using relativedelta)
"""

from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from django.utils import timezone
from gnfs_komenda.models import PublicHoliday, LeaveType


def get_public_holidays_set(start_year, end_year):
    """Return set of active public holiday dates for given range of years."""
    return set(
        PublicHoliday.objects.filter(
            active=True,
            year__gte=start_year,
            year__lte=end_year
        ).values_list('date', flat=True)
    )


def is_working_day(target_date, holiday_set=None):
    """
    Check if target_date is a valid working day (Monday - Friday and not a holiday).
    """
    if target_date.weekday() >= 5:  # 5=Saturday, 6=Sunday
        return False
    if holiday_set is not None:
        return target_date not in holiday_set
    return not PublicHoliday.objects.filter(date=target_date, active=True).exists()


def get_next_working_day(target_date, holiday_set=None):
    """Find the first working day on or after target_date."""
    if holiday_set is None:
        holiday_set = get_public_holidays_set(target_date.year, target_date.year + 1)
    
    current = target_date
    # Safeguard max loop of 30 days
    for _ in range(30):
        if is_working_day(current, holiday_set):
            return current
        current += timedelta(days=1)
    return current


def calculate_leave_dates(start_date, duration, calculation_mode, end_date=None):
    """
    Calculate last_leave_date, resume_date, and actual working/calendar days.

    Args:
        start_date (date): Starting date of leave.
        duration (int): Duration in days or months (depending on calculation_mode).
        calculation_mode (str): One of WORKING_DAYS, CALENDAR_DAYS, DATE_RANGE, CALENDAR_MONTHS.
        end_date (date, optional): Required if calculation_mode is DATE_RANGE.

    Returns:
        dict: {
            'start_date': date,
            'last_leave_date': date,
            'resume_date': date,
            'duration': int,
            'working_days_count': int,
            'calendar_days_count': int,
            'holidays_encountered': list[str],
            'weekend_days_count': int,
        }
    """
    if not isinstance(start_date, date):
        raise ValueError("start_date must be a datetime.date instance")

    start_year = start_date.year
    # Fetch holidays for range
    holiday_set = get_public_holidays_set(start_year - 1, start_year + 2)
    holidays_encountered = []
    weekend_days_count = 0
    working_days_count = 0

    if calculation_mode == LeaveType.CalculationMode.WORKING_DAYS:
        if duration <= 0:
            raise ValueError("Duration must be at least 1 working day")

        current = start_date
        counted = 0
        last_date = start_date

        while counted < duration:
            if current.weekday() >= 5:
                weekend_days_count += 1
            elif current in holiday_set:
                holidays_encountered.append(current.isoformat())
            else:
                counted += 1
                last_date = current
                working_days_count += 1
                if counted == duration:
                    break
            current += timedelta(days=1)

        last_leave_date = last_date
        resume_date = get_next_working_day(last_leave_date + timedelta(days=1), holiday_set)
        calendar_days_count = (last_leave_date - start_date).days + 1

    elif calculation_mode == LeaveType.CalculationMode.CALENDAR_DAYS:
        if duration <= 0:
            raise ValueError("Duration must be at least 1 day")

        last_leave_date = start_date + timedelta(days=duration - 1)
        resume_date = get_next_working_day(last_leave_date + timedelta(days=1), holiday_set)
        calendar_days_count = duration

        # Count working days & weekends in range
        cur = start_date
        while cur <= last_leave_date:
            if cur.weekday() >= 5:
                weekend_days_count += 1
            elif cur in holiday_set:
                holidays_encountered.append(cur.isoformat())
            else:
                working_days_count += 1
            cur += timedelta(days=1)

    elif calculation_mode == LeaveType.CalculationMode.DATE_RANGE:
        if not end_date:
            raise ValueError("end_date is required for DATE_RANGE calculation mode")
        if end_date < start_date:
            raise ValueError("end_date cannot be earlier than start_date")

        last_leave_date = end_date
        calendar_days_count = (last_leave_date - start_date).days + 1
        duration = calendar_days_count
        resume_date = get_next_working_day(last_leave_date + timedelta(days=1), holiday_set)

        cur = start_date
        while cur <= last_leave_date:
            if cur.weekday() >= 5:
                weekend_days_count += 1
            elif cur in holiday_set:
                holidays_encountered.append(cur.isoformat())
            else:
                working_days_count += 1
            cur += timedelta(days=1)

    elif calculation_mode == LeaveType.CalculationMode.CALENDAR_MONTHS:
        if duration <= 0:
            raise ValueError("Duration in months must be at least 1")

        # Exact calendar month arithmetic
        last_leave_date = start_date + relativedelta(months=duration) - timedelta(days=1)
        resume_date = get_next_working_day(last_leave_date + timedelta(days=1), holiday_set)
        calendar_days_count = (last_leave_date - start_date).days + 1

        cur = start_date
        while cur <= last_leave_date:
            if cur.weekday() >= 5:
                weekend_days_count += 1
            elif cur in holiday_set:
                holidays_encountered.append(cur.isoformat())
            else:
                working_days_count += 1
            cur += timedelta(days=1)

    else:
        raise ValueError(f"Unsupported calculation mode: {calculation_mode}")

    return {
        'start_date': start_date,
        'last_leave_date': last_leave_date,
        'resume_date': resume_date,
        'duration': duration,
        'working_days_count': working_days_count,
        'calendar_days_count': calendar_days_count,
        'holidays_encountered': holidays_encountered,
        'weekend_days_count': weekend_days_count,
    }
