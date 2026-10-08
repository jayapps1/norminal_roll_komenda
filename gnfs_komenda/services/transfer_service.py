"""
Enterprise Personnel Transfer & Posting Management Service for GNFS.
Implements the full transfer state-machine:
DRAFT -> PENDING_APPROVAL -> APPROVED -> RELEASED -> REPORTED -> CONFIRMED (or CANCELLED/REJECTED).
Strictly enforces:
TRANSFER CREATED != OFFICER MOVED
OFFICER RELEASED != ACTIVE POSTING CHANGED
ACTIVE POSTING CHANGES ONLY UPON RECEIVING COMMAND ARRIVAL CONFIRMATION.
"""

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone
from gnfs_komenda.models import (
    PersonnelTransfer, TransferStatus, TransferType,
    TransferApprovalHistory, NorminalRoll, Region, District,
    Station, Post, FunctionalUnit, AuditLog, Notification,
    CommandAppointment
)
from gnfs_komenda.services.posting_service import create_new_posting
from gnfs_komenda.services.functional_assignment_service import assign_officer_functional_unit
from gnfs_komenda.security import get_user_role, get_user_assignment


def generate_transfer_reference():
    """Generate unique transfer reference e.g., TRF-2026-000001."""
    year = timezone.now().year
    prefix = f"TRF-{year}-"
    last_transfer = PersonnelTransfer.objects.filter(reference_number__startswith=prefix).order_by('-id').first()
    if last_transfer and last_transfer.reference_number:
        try:
            last_seq = int(last_transfer.reference_number.split('-')[-1])
            new_seq = last_seq + 1
        except ValueError:
            new_seq = 1
    else:
        new_seq = 1
    return f"{prefix}{new_seq:06d}"


def resolve_transfer_type(source_region, source_district, source_station, source_post,
                          destination_region, destination_district, destination_station, destination_post,
                          has_functional_change=False):
    """
    Automatically deduce the transfer classification from source and destination hierarchy.
    """
    if source_region != destination_region:
        base = TransferType.INTER_REGIONAL
    elif source_district != destination_district:
        base = TransferType.INTER_DISTRICT
    elif source_station != destination_station:
        base = TransferType.INTER_STATION
    elif source_post != destination_post:
        base = TransferType.INTER_POST
    elif has_functional_change:
        return TransferType.FUNCTIONAL_TRANSFER
    else:
        base = TransferType.INTER_STATION

    if has_functional_change:
        return TransferType.COMBINED_TRANSFER
    return base


def create_transfer(personnel, user, destination_region,
                    destination_district=None, destination_station=None, destination_post=None,
                    destination_functional_unit=None, transfer_letter_number="",
                    transfer_letter_date=None, effective_date=None,
                    expected_reporting_date=None, reason="", remarks="",
                    attachment=None, submit_now=False):
    """
    Initiate a new personnel transfer record.
    Active posting remains UNCHANGED on source command.
    """
    # Prevent concurrent active transfers for the same officer
    active_exists = PersonnelTransfer.objects.filter(
        personnel=personnel,
        status__in=[
            TransferStatus.DRAFT,
            TransferStatus.PENDING_APPROVAL,
            TransferStatus.APPROVED,
            TransferStatus.RELEASED,
            TransferStatus.REPORTED,
        ]
    ).exists()
    if active_exists:
        raise ValidationError(f"Officer {personnel.fullname} already has an ongoing active transfer.")

    # Derive source command posting
    source_station = personnel.station
    source_post = personnel.post
    source_district = source_station.district if source_station else None
    source_region = source_district.region if source_district else None
    source_functional_unit = personnel.functional_unit

    if not source_region:
        raise ValidationError("Cannot initiate transfer: Officer has no assigned source Region.")

    has_func_change = bool(destination_functional_unit and destination_functional_unit != source_functional_unit)
    has_cmd_change = (
        source_region != destination_region or
        (destination_district and source_district != destination_district) or
        (destination_station and source_station != destination_station) or
        (destination_post and source_post != destination_post)
    )
    if not has_cmd_change and not has_func_change and destination_station == source_station:
        raise ValidationError("Cannot initiate transfer: Destination is identical to current command station and functional assignment.")

    ttype = resolve_transfer_type(
        source_region, source_district, source_station, source_post,
        destination_region, destination_district, destination_station, destination_post,
        has_functional_change=has_func_change
    )

    now = timezone.now()
    if not effective_date:
        effective_date = now.date()
    if not expected_reporting_date:
        expected_reporting_date = effective_date

    ref = generate_transfer_reference()
    status = TransferStatus.PENDING_APPROVAL if submit_now else TransferStatus.DRAFT

    transfer = PersonnelTransfer.objects.create(
        reference_number=ref,
        personnel=personnel,
        transfer_type=ttype,
        source_region=source_region,
        source_district=source_district,
        source_station=source_station,
        source_post=source_post,
        source_functional_unit=source_functional_unit,
        destination_region=destination_region,
        destination_district=destination_district,
        destination_station=destination_station,
        destination_post=destination_post,
        destination_functional_unit=destination_functional_unit,
        transfer_letter_number=transfer_letter_number or ref,
        transfer_letter_date=transfer_letter_date,
        effective_date=effective_date,
        expected_reporting_date=expected_reporting_date,
        attachment=attachment,
        reason=reason,
        remarks=remarks,
        status=status,
        initiated_by=user,
        initiated_at=now
    )

    TransferApprovalHistory.objects.create(
        transfer=transfer,
        actor=user,
        from_status="NONE",
        to_status=status,
        action="CREATE_AND_SUBMIT" if submit_now else "CREATE_DRAFT",
        comments=remarks or "Transfer created."
    )

    return transfer


def submit_transfer(transfer, user, comments=""):
    """Move transfer from DRAFT to PENDING_APPROVAL."""
    if transfer.status != TransferStatus.DRAFT:
        raise ValidationError("Only draft transfers can be submitted.")

    old_status = transfer.status
    transfer.status = TransferStatus.PENDING_APPROVAL
    transfer.save(update_fields=['status', 'updated_at'])

    TransferApprovalHistory.objects.create(
        transfer=transfer,
        actor=user,
        from_status=old_status,
        to_status=transfer.status,
        action="SUBMIT_FOR_APPROVAL",
        comments=comments or "Submitted for command approval."
    )
    return transfer


def approve_transfer(transfer, user, comments=""):
    """Approve a pending transfer."""
    if transfer.status != TransferStatus.PENDING_APPROVAL:
        raise ValidationError("Only pending transfers can be approved.")

    old_status = transfer.status
    transfer.status = TransferStatus.APPROVED
    transfer.save(update_fields=['status', 'updated_at'])

    TransferApprovalHistory.objects.create(
        transfer=transfer,
        actor=user,
        from_status=old_status,
        to_status=transfer.status,
        action="APPROVE_TRANSFER",
        comments=comments or "Transfer approved by authority."
    )
    return transfer


def release_officer(transfer, user, comments=""):
    """
    Release officer from source command.
    CRITICAL: Officer active posting does NOT change to destination yet.
    Status moves to RELEASED (Awaiting Reporting).
    """
    if transfer.status not in (TransferStatus.APPROVED, TransferStatus.PENDING_APPROVAL):
        raise ValidationError("Transfer must be approved before releasing officer.")

    old_status = transfer.status
    now = timezone.now()
    transfer.status = TransferStatus.RELEASED
    transfer.released_by = user
    transfer.released_at = now
    transfer.save(update_fields=['status', 'released_by', 'released_at', 'updated_at'])

    TransferApprovalHistory.objects.create(
        transfer=transfer,
        actor=user,
        from_status=old_status,
        to_status=transfer.status,
        action="RELEASE_OFFICER",
        comments=comments or f"Officer released from {transfer.source_station or transfer.source_region}."
    )

    # Notify destination command
    Notification.objects.create(
        recipient=user,
        title=f"Officer Released: {transfer.personnel.fullname}",
        message=f"Transfer {transfer.reference_number}: {transfer.personnel.fullname} released. Expected reporting on {transfer.expected_reporting_date}.",
        notification_type="TRANSFER",
        link_url=f"/transfers/incoming/{transfer.id}/"
    )
    return transfer


def mark_reported(transfer, user, actual_reporting_date=None, comments=""):
    """
    Mark that officer has physically reported at destination, awaiting administrative confirmation.
    """
    if transfer.status != TransferStatus.RELEASED:
        raise ValidationError("Officer must be in RELEASED status before marking as reported.")

    if not actual_reporting_date:
        actual_reporting_date = timezone.now().date()

    old_status = transfer.status
    now = timezone.now()
    transfer.status = TransferStatus.REPORTED
    transfer.reported_by = user
    transfer.reported_at = now
    transfer.actual_reporting_date = actual_reporting_date
    transfer.save(update_fields=['status', 'reported_by', 'reported_at', 'actual_reporting_date', 'updated_at'])

    TransferApprovalHistory.objects.create(
        transfer=transfer,
        actor=user,
        from_status=old_status,
        to_status=transfer.status,
        action="MARK_REPORTED",
        comments=comments or f"Officer reported at destination on {actual_reporting_date}."
    )
    return transfer


def complete_destination_placement(transfer, user, destination_district=None,
                                   destination_station=None, destination_post=None,
                                   destination_functional_unit=None, comments=""):
    """
    Allows receiving command (e.g. Regional administration) to specify the exact
    District, Station, and Post before or during confirmation.
    Cannot alter source hierarchy.
    """
    if transfer.status in (TransferStatus.CONFIRMED, TransferStatus.CANCELLED):
        raise ValidationError("Cannot modify destination on completed or cancelled transfers.")

    # Validate hierarchy consistency
    if destination_station and destination_district and destination_station.district != destination_district:
        raise ValidationError("Destination station does not belong to selected district.")
    if destination_station and destination_station.district.region != transfer.destination_region:
        raise ValidationError("Destination station does not belong to destination region.")

    transfer.destination_district = destination_district
    transfer.destination_station = destination_station
    transfer.destination_post = destination_post
    if destination_functional_unit:
        transfer.destination_functional_unit = destination_functional_unit

    transfer.save(update_fields=[
        'destination_district', 'destination_station', 'destination_post',
        'destination_functional_unit', 'updated_at'
    ])

    TransferApprovalHistory.objects.create(
        transfer=transfer,
        actor=user,
        from_status=transfer.status,
        to_status=transfer.status,
        action="COMPLETE_DESTINATION_PLACEMENT",
        comments=comments or f"Destination assigned: Station: {destination_station}, District: {destination_district}."
    )
    return transfer


def check_transfer_command_appointments(personnel):
    """Check if personnel holds any active command appointments that must be resolved."""
    return CommandAppointment.objects.filter(
        personnel=personnel,
        is_active=True
    ).select_related('region', 'district', 'station')


def confirm_arrival(transfer, user, actual_reporting_date=None, comments="", resolve_appointments=False):
    """
    Confirm arrival at destination command.
    THIS IS THE FINAL STEP THAT ACTIVATES THE NEW COMMAND POSTING AND CLOSES THE OLD ONE.
    Executed in transaction.atomic() with select_for_update().
    Detects any active command appointments (RFO, 2IC, DFO, MFO) and requires resolution,
    ensuring command titles are never silently transferred to destination commands.
    """
    if not actual_reporting_date:
        actual_reporting_date = timezone.now().date()

    with transaction.atomic():
        locked_transfer = PersonnelTransfer.objects.select_for_update().get(id=transfer.id)
        personnel = NorminalRoll.objects.select_for_update().get(id=locked_transfer.personnel_id)

        if locked_transfer.status not in (TransferStatus.RELEASED, TransferStatus.REPORTED):
            raise ValidationError(f"Cannot confirm transfer in status '{locked_transfer.status}'. Must be Released or Reported.")

        if not locked_transfer.destination_station and locked_transfer.transfer_type != TransferType.FUNCTIONAL_TRANSFER:
            # Regional-level transfers require station assignment before final confirmation
            raise ValidationError("Please assign destination Fire Station before confirming arrival.")

        # Check for active command appointments
        if locked_transfer.transfer_type != TransferType.FUNCTIONAL_TRANSFER:
            active_appointments = CommandAppointment.objects.filter(personnel=personnel, is_active=True)
            if active_appointments.exists():
                if not resolve_appointments:
                    appt_strs = [
                        f"{a.get_appointment_type_display()} ({a.district.name if a.district else (a.region.name if a.region else 'Command')})"
                        for a in active_appointments
                    ]
                    raise ValidationError(
                        f"This officer currently holds an active command appointment: {', '.join(appt_strs)}. "
                        f"The appointment must be resolved before this transfer can be completed."
                    )
                else:
                    for appt in active_appointments:
                        appt.end_appointment(
                            user=user,
                            end_date=actual_reporting_date,
                            remarks=f"Command appointment resolved due to arrival confirmation on transfer {locked_transfer.reference_number}."
                        )

        old_status = locked_transfer.status
        now = timezone.now()

        # 1. Update transfer record
        locked_transfer.status = TransferStatus.CONFIRMED
        locked_transfer.confirmed_by = user
        locked_transfer.confirmed_at = now
        locked_transfer.actual_reporting_date = actual_reporting_date
        locked_transfer.save(update_fields=['status', 'confirmed_by', 'confirmed_at', 'actual_reporting_date', 'updated_at'])

        # 2. Activate new Command Posting and close old posting
        if locked_transfer.transfer_type != TransferType.FUNCTIONAL_TRANSFER:
            create_new_posting(
                personnel=personnel,
                region=locked_transfer.destination_region,
                district=locked_transfer.destination_district,
                station=locked_transfer.destination_station,
                post=locked_transfer.destination_post,
                start_date=actual_reporting_date,
                assigned_by=locked_transfer.initiated_by,
                confirmed_by=user,
                source_transfer=locked_transfer,
                remarks=f"Confirmed arrival from {locked_transfer.reference_number}. Prev station: {locked_transfer.source_station}."
            )

        # 3. Activate new Functional Placement if specified
        if locked_transfer.destination_functional_unit:
            assign_officer_functional_unit(
                personnel=personnel,
                functional_unit=locked_transfer.destination_functional_unit,
                start_date=actual_reporting_date,
                assigned_by=user,
                approved_by=user,
                source_transfer=locked_transfer,
                reason=f"Functional placement from transfer {locked_transfer.reference_number}."
            )

        # 4. Record transition history
        TransferApprovalHistory.objects.create(
            transfer=locked_transfer,
            actor=user,
            from_status=old_status,
            to_status=locked_transfer.status,
            action="CONFIRM_ARRIVAL",
            comments=comments or f"Arrival confirmed by {user.username}. New posting is now ACTIVE."
        )

        # 5. Audit Log
        AuditLog.objects.create(
            user=user,
            action="CONFIRM_PERSONNEL_TRANSFER",
            target_type="NorminalRoll",
            target_id=str(personnel.id),
            description=f"Transfer {locked_transfer.reference_number} confirmed for {personnel.fullname}. New station: {locked_transfer.destination_station}.",
            old_values={"station": str(locked_transfer.source_station), "region": str(locked_transfer.source_region)},
            new_values={"station": str(locked_transfer.destination_station), "region": str(locked_transfer.destination_region)},
            region=locked_transfer.destination_region,
            district=locked_transfer.destination_district,
            station=locked_transfer.destination_station
        )

        return locked_transfer


def cancel_transfer(transfer, user, reason=""):
    """
    Cancel a transfer before completion. Never deletes the record.
    """
    if transfer.status == TransferStatus.CONFIRMED:
        raise ValidationError("Cannot cancel a completed/confirmed transfer. A return transfer must be initiated instead.")

    old_status = transfer.status
    now = timezone.now()
    transfer.status = TransferStatus.CANCELLED
    transfer.cancelled_by = user
    transfer.cancelled_at = now
    transfer.cancellation_reason = reason or "Cancelled by administrator."
    transfer.save(update_fields=['status', 'cancelled_by', 'cancelled_at', 'cancellation_reason', 'updated_at'])

    TransferApprovalHistory.objects.create(
        transfer=transfer,
        actor=user,
        from_status=old_status,
        to_status=transfer.status,
        action="CANCEL_TRANSFER",
        comments=reason or "Transfer cancelled."
    )
    return transfer
