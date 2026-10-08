"""
Command Appointment Service for GNFS Personnel.
Manages formal command leadership appointments (RFO, Regional 2IC, DFO, MFO, District 2IC)
with full audit logging, history retention, and atomic single-active enforcement.
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from gnfs_komenda.models import (
    CommandAppointment,
    NorminalRoll,
    Region,
    District,
    Station,
    Post,
    AuditLog,
)


def _log_command_appointment_audit(user, action, appointment, description, old_values=None, new_values=None):
    """Internal helper to write audit entries for command appointment transitions."""
    AuditLog.objects.create(
        user=user if user and getattr(user, 'is_authenticated', False) else None,
        action=action,
        target_type="CommandAppointment",
        target_id=str(appointment.id),
        description=description,
        old_values=old_values,
        new_values=new_values,
        region=appointment.region,
        district=appointment.district,
        station=appointment.station,
        post=appointment.post,
    )


def appoint_rfo(region, personnel, effective_from=None, appointed_by=None,
                appointment_reference="", remarks="", is_acting=False):
    """
    Appoint an officer as RFO (or Acting RFO) for a Region.
    Atomically closes any existing active RFO / Acting RFO for the region.
    Never deletes historical appointment records.
    """
    if not region or not personnel:
        raise ValidationError("Both Region and Personnel must be specified.")

    if not effective_from:
        effective_from = timezone.now().date()

    apt_type = (
        CommandAppointment.AppointmentType.ACTING_RFO
        if is_acting
        else CommandAppointment.AppointmentType.RFO
    )

    with transaction.atomic():
        # 1. Close existing active RFO appointments for this region
        active_prior = CommandAppointment.objects.filter(
            region=region,
            is_active=True,
            appointment_type__in=[
                CommandAppointment.AppointmentType.RFO,
                CommandAppointment.AppointmentType.ACTING_RFO,
            ]
        )
        old_values = []
        for prior in active_prior:
            old_values.append({
                "id": prior.id,
                "personnel": prior.personnel.fullname,
                "staffID": prior.personnel.staffID,
                "type": prior.appointment_type,
                "effective_from": str(prior.effective_from),
            })
            prior.end_appointment(
                user=appointed_by,
                end_date=effective_from,
                remarks=f"Superseded by appointment of {personnel.fullname} as {apt_type}."
            )

        # 2. Create the new appointment
        appointment = CommandAppointment.objects.create(
            personnel=personnel,
            appointment_type=apt_type,
            region=region,
            district=None,
            station=None,
            post=None,
            effective_from=effective_from,
            is_active=True,
            is_acting=is_acting,
            appointed_by=appointed_by,
            appointment_reference=appointment_reference,
            remarks=remarks,
        )

        # 3. Write audit log
        desc = (
            f"Appointed {personnel.fullname} ({personnel.staffID}) as "
            f"{'Acting ' if is_acting else ''}Regional Fire Officer for {region.name}."
        )
        new_values = {
            "id": appointment.id,
            "personnel": personnel.fullname,
            "staffID": personnel.staffID,
            "type": apt_type,
            "effective_from": str(effective_from),
            "reference": appointment_reference,
        }
        _log_command_appointment_audit(
            user=appointed_by,
            action="APPOINT_RFO",
            appointment=appointment,
            description=desc,
            old_values=old_values,
            new_values=new_values
        )

        return appointment


def appoint_regional_2ic(region, personnel, effective_from=None, appointed_by=None,
                         appointment_reference="", remarks="", is_acting=False):
    """
    Appoint an officer as Regional 2IC (or Acting Regional 2IC) for a Region.
    Atomically closes any existing active 2IC for the region.
    """
    if not region or not personnel:
        raise ValidationError("Both Region and Personnel must be specified.")

    if not effective_from:
        effective_from = timezone.now().date()

    apt_type = (
        CommandAppointment.AppointmentType.ACTING_REGIONAL_2IC
        if is_acting
        else CommandAppointment.AppointmentType.REGIONAL_2IC
    )

    with transaction.atomic():
        active_prior = CommandAppointment.objects.filter(
            region=region,
            is_active=True,
            appointment_type__in=[
                CommandAppointment.AppointmentType.REGIONAL_2IC,
                CommandAppointment.AppointmentType.ACTING_REGIONAL_2IC,
            ]
        )
        old_values = []
        for prior in active_prior:
            old_values.append({
                "id": prior.id,
                "personnel": prior.personnel.fullname,
                "staffID": prior.personnel.staffID,
                "type": prior.appointment_type,
                "effective_from": str(prior.effective_from),
            })
            prior.end_appointment(
                user=appointed_by,
                end_date=effective_from,
                remarks=f"Superseded by appointment of {personnel.fullname} as {apt_type}."
            )

        appointment = CommandAppointment.objects.create(
            personnel=personnel,
            appointment_type=apt_type,
            region=region,
            district=None,
            station=None,
            post=None,
            effective_from=effective_from,
            is_active=True,
            is_acting=is_acting,
            appointed_by=appointed_by,
            appointment_reference=appointment_reference,
            remarks=remarks,
        )

        desc = (
            f"Appointed {personnel.fullname} ({personnel.staffID}) as "
            f"{'Acting ' if is_acting else ''}Regional 2IC for {region.name}."
        )
        new_values = {
            "id": appointment.id,
            "personnel": personnel.fullname,
            "staffID": personnel.staffID,
            "type": apt_type,
            "effective_from": str(effective_from),
            "reference": appointment_reference,
        }
        _log_command_appointment_audit(
            user=appointed_by,
            action="APPOINT_REGIONAL_2IC",
            appointment=appointment,
            description=desc,
            old_values=old_values,
            new_values=new_values
        )

        return appointment


def appoint_commanding_officer(district, personnel, appointment_type=None,
                               effective_from=None, appointed_by=None,
                               appointment_reference="", remarks="", is_acting=False):
    """
    Appoint an officer as District/Municipal/Metropolitan Commanding Officer (DFO/MFO/METRO_FO).
    Atomically closes prior active commanding officer appointments for this district command.
    """
    if not district or not personnel:
        raise ValidationError("Both District and Personnel must be specified.")

    if not effective_from:
        effective_from = timezone.now().date()

    if not appointment_type:
        base_desig = district.co_designation or "DFO"
        if is_acting:
            appointment_type = f"ACTING_{base_desig}"
        else:
            appointment_type = base_desig

    with transaction.atomic():
        active_prior = CommandAppointment.objects.filter(
            district=district,
            is_active=True,
            appointment_type__in=[
                CommandAppointment.AppointmentType.DFO,
                CommandAppointment.AppointmentType.MFO,
                CommandAppointment.AppointmentType.METRO_FO,
                CommandAppointment.AppointmentType.ACTING_DFO,
                CommandAppointment.AppointmentType.ACTING_MFO,
                CommandAppointment.AppointmentType.ACTING_METRO_FO,
            ]
        )
        old_values = []
        for prior in active_prior:
            old_values.append({
                "id": prior.id,
                "personnel": prior.personnel.fullname,
                "staffID": prior.personnel.staffID,
                "type": prior.appointment_type,
                "effective_from": str(prior.effective_from),
            })
            prior.end_appointment(
                user=appointed_by,
                end_date=effective_from,
                remarks=f"Superseded by appointment of {personnel.fullname} as {appointment_type}."
            )

        appointment = CommandAppointment.objects.create(
            personnel=personnel,
            appointment_type=appointment_type,
            region=district.region,
            district=district,
            station=None,
            post=None,
            effective_from=effective_from,
            is_active=True,
            is_acting=is_acting,
            appointed_by=appointed_by,
            appointment_reference=appointment_reference,
            remarks=remarks,
        )

        desc = (
            f"Appointed {personnel.fullname} ({personnel.staffID}) as "
            f"{'Acting ' if is_acting else ''}{appointment_type} for {district.name}."
        )
        new_values = {
            "id": appointment.id,
            "personnel": personnel.fullname,
            "staffID": personnel.staffID,
            "type": appointment_type,
            "effective_from": str(effective_from),
            "reference": appointment_reference,
        }
        _log_command_appointment_audit(
            user=appointed_by,
            action="APPOINT_COMMANDING_OFFICER",
            appointment=appointment,
            description=desc,
            old_values=old_values,
            new_values=new_values
        )

        return appointment


def appoint_dfo(district, personnel, effective_from=None, appointed_by=None,
                appointment_reference="", remarks="", is_acting=False):
    """Convenience shortcut to appoint DFO."""
    apt_type = (
        CommandAppointment.AppointmentType.ACTING_DFO
        if is_acting
        else CommandAppointment.AppointmentType.DFO
    )
    return appoint_commanding_officer(
        district=district,
        personnel=personnel,
        appointment_type=apt_type,
        effective_from=effective_from,
        appointed_by=appointed_by,
        appointment_reference=appointment_reference,
        remarks=remarks,
        is_acting=is_acting
    )


def appoint_mfo(district, personnel, effective_from=None, appointed_by=None,
                appointment_reference="", remarks="", is_acting=False):
    """Convenience shortcut to appoint MFO."""
    apt_type = (
        CommandAppointment.AppointmentType.ACTING_MFO
        if is_acting
        else CommandAppointment.AppointmentType.MFO
    )
    return appoint_commanding_officer(
        district=district,
        personnel=personnel,
        appointment_type=apt_type,
        effective_from=effective_from,
        appointed_by=appointed_by,
        appointment_reference=appointment_reference,
        remarks=remarks,
        is_acting=is_acting
    )


def appoint_district_2ic(district, personnel, effective_from=None, appointed_by=None,
                         appointment_reference="", remarks="", is_acting=False):
    """
    Appoint an officer as District/Municipal/Metropolitan Second-in-Command (2IC).
    Atomically closes prior active 2IC appointments for this district command.
    """
    if not district or not personnel:
        raise ValidationError("Both District and Personnel must be specified.")

    if not effective_from:
        effective_from = timezone.now().date()

    apt_type = (
        CommandAppointment.AppointmentType.ACTING_DISTRICT_2IC
        if is_acting
        else CommandAppointment.AppointmentType.DISTRICT_2IC
    )

    with transaction.atomic():
        active_prior = CommandAppointment.objects.filter(
            district=district,
            is_active=True,
            appointment_type__in=[
                CommandAppointment.AppointmentType.DISTRICT_2IC,
                CommandAppointment.AppointmentType.ACTING_DISTRICT_2IC,
            ]
        )
        old_values = []
        for prior in active_prior:
            old_values.append({
                "id": prior.id,
                "personnel": prior.personnel.fullname,
                "staffID": prior.personnel.staffID,
                "type": prior.appointment_type,
                "effective_from": str(prior.effective_from),
            })
            prior.end_appointment(
                user=appointed_by,
                end_date=effective_from,
                remarks=f"Superseded by appointment of {personnel.fullname} as {apt_type}."
            )

        appointment = CommandAppointment.objects.create(
            personnel=personnel,
            appointment_type=apt_type,
            region=district.region,
            district=district,
            station=None,
            post=None,
            effective_from=effective_from,
            is_active=True,
            is_acting=is_acting,
            appointed_by=appointed_by,
            appointment_reference=appointment_reference,
            remarks=remarks,
        )

        desc = (
            f"Appointed {personnel.fullname} ({personnel.staffID}) as "
            f"{'Acting ' if is_acting else ''}District 2IC for {district.name}."
        )
        new_values = {
            "id": appointment.id,
            "personnel": personnel.fullname,
            "staffID": personnel.staffID,
            "type": apt_type,
            "effective_from": str(effective_from),
            "reference": appointment_reference,
        }
        _log_command_appointment_audit(
            user=appointed_by,
            action="APPOINT_DISTRICT_2IC",
            appointment=appointment,
            description=desc,
            old_values=old_values,
            new_values=new_values
        )

        return appointment


def end_command_appointment(appointment, user=None, end_date=None, remarks=""):
    """
    End an active command appointment with full audit recording.
    """
    if not appointment:
        raise ValidationError("Appointment must be provided.")

    if not appointment.is_active:
        return appointment

    with transaction.atomic():
        appointment.end_appointment(user=user, end_date=end_date, remarks=remarks)
        _log_command_appointment_audit(
            user=user,
            action="END_COMMAND_APPOINTMENT",
            appointment=appointment,
            description=(
                f"Ended command appointment {appointment.get_appointment_type_display()} for "
                f"{appointment.personnel.fullname} ({appointment.personnel.staffID}). Reason: {remarks}"
            ),
            old_values={"is_active": True},
            new_values={"is_active": False, "effective_to": str(appointment.effective_to)}
        )
        return appointment


def check_officer_active_command_appointments(personnel):
    """
    Return QuerySet of active command appointments held by personnel.
    """
    return CommandAppointment.objects.filter(
        personnel=personnel,
        is_active=True
    ).select_related('region', 'district', 'station')


def get_officer_command_history(personnel):
    """
    Return all command appointment records for personnel, newest first.
    """
    return CommandAppointment.objects.filter(
        personnel=personnel
    ).select_related('region', 'district', 'station', 'appointed_by').order_by('-effective_from', '-created_at')


def get_region_leadership_history(region):
    """
    Return full leadership history for a Region (RFO & 2IC), newest first.
    """
    return CommandAppointment.objects.filter(
        region=region,
        district__isnull=True
    ).select_related('personnel', 'appointed_by').order_by('-effective_from', '-created_at')


def get_district_leadership_history(district):
    """
    Return full leadership history for a District Command (CO & 2IC), newest first.
    """
    return CommandAppointment.objects.filter(
        district=district
    ).select_related('personnel', 'appointed_by').order_by('-effective_from', '-created_at')
