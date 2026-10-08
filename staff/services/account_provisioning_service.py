import os
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from staff.models import UserAssignment, GNFSUserRole
from gnfs_komenda.models import NorminalRoll, AuditLog
from gnfs_komenda.security import send_notification

User = get_user_model()


def get_temporary_password():
    """Return the configured initial temporary password for newly provisioned personnel accounts."""
    return getattr(settings, "GNFS_INITIAL_PASSWORD", os.getenv("GNFS_INITIAL_PASSWORD", "@Jay0697"))


def is_system_protected_account(user):
    """
    Return True if the user is a bootstrap or technical system administration account
    that must never be modified, provisioned, or reset through personnel administration.
    """
    if not user:
        return False
    if user.is_superuser:
        return True
    assignment = getattr(user, "assignment", None)
    if assignment and assignment.is_system_account:
        return True
    # If the user is staff without a personnel record, protect it as a system account
    if user.is_staff and (not assignment or not assignment.personnel):
        return True
    return False


def provision_personnel_account(personnel, temporary_password=None, created_by=None):
    """
    Provision a single personnel-linked User account using their Staff ID as the username.
    Assigns the configured temporary password (hashed) and sets must_change_password=True.
    Idempotent and safe: never overwrites existing passwords or protected system accounts.

    Returns:
        tuple: (user, created_boolean, message_string)
    """
    if not personnel or not personnel.staffID:
        raise ValidationError("Personnel record must possess a valid Staff ID to provision an account.")

    staff_id = personnel.staffID.strip()

    # 1. Check if personnel already has a linked User
    existing_assignment = UserAssignment.objects.filter(personnel=personnel).select_related('user').first()
    if existing_assignment and existing_assignment.user:
        return existing_assignment.user, False, "Personnel is already linked to an existing user account."

    # 2. Check if a User with this username/Staff ID already exists
    existing_user = User.objects.filter(username__iexact=staff_id).first()
    if existing_user:
        # Protect system accounts
        if is_system_protected_account(existing_user):
            return None, False, f"Username '{staff_id}' conflicts with a protected system administrator account."

        # Check if already linked to another personnel
        existing_other = UserAssignment.objects.filter(user=existing_user).exclude(personnel=personnel).first()
        if existing_other and existing_other.personnel:
            return None, False, f"Username '{staff_id}' is already linked to personnel {existing_other.personnel.fullname}."

        # Safely link existing user without resetting password or TOTP
        with transaction.atomic():
            asgn, _ = UserAssignment.objects.get_or_create(
                user=existing_user,
                defaults={
                    "personnel": personnel,
                    "role": GNFSUserRole.STAFF,
                    "must_change_password": False,
                    "is_system_account": False,
                    "active": True
                }
            )
            if not asgn.personnel:
                asgn.personnel = personnel
                asgn.save(update_fields=['personnel'])

            AuditLog.objects.create(
                user=created_by,
                action="PERSONNEL_ACCOUNT_LINKED",
                target_type="User",
                target_id=str(existing_user.id),
                description=f"Safely linked existing user '{staff_id}' to personnel {personnel.fullname} without altering password.",
                station=personnel.station,
                district=personnel.current_district,
                region=personnel.current_region
            )
        return existing_user, False, "Existing account safely linked without credential modification."

    # 3. Create newly provisioned personnel-linked account
    temp_pw = temporary_password or get_temporary_password()

    with transaction.atomic():
        user = User(
            username=staff_id,
            is_staff=False,
            is_superuser=False,
            is_active=True
        )
        user.set_password(temp_pw)  # Password hashed using Django PBKDF2
        user.save()

        UserAssignment.objects.create(
            user=user,
            personnel=personnel,
            role=GNFSUserRole.STAFF,
            must_change_password=True,
            is_system_account=False,
            active=True
        )

        AuditLog.objects.create(
            user=created_by,
            action="PERSONNEL_ACCOUNT_CREATED",
            target_type="User",
            target_id=str(user.id),
            description=f"Provisioned personnel-linked account for {personnel.fullname} (Staff ID: {staff_id}) with temporary password and mandatory first-login password change required.",
            station=personnel.station,
            district=personnel.current_district,
            region=personnel.current_region
        )

    return user, True, "Account created successfully with temporary password."


def bulk_provision_personnel_accounts(personnel_queryset=None, temporary_password=None, dry_run=False):
    """
    Bulk provision user accounts for eligible personnel records.
    Explicitly excludes seeded super admins, bootstrap accounts, and already-linked personnel.
    Idempotent across multiple runs.
    """
    if personnel_queryset is None:
        personnel_queryset = NorminalRoll.objects.filter(is_active=True).exclude(
            staffID__isnull=True
        ).exclude(staffID="")

    results = {
        "examined": 0,
        "created": 0,
        "already_linked": 0,
        "existing_skipped": 0,
        "system_skipped": 0,
        "errors": 0,
        "details": []
    }

    for personnel in personnel_queryset:
        results["examined"] += 1
        staff_id = personnel.staffID.strip()

        # Check existing linkage
        existing_assignment = UserAssignment.objects.filter(personnel=personnel).first()
        if existing_assignment and existing_assignment.user:
            results["already_linked"] += 1
            continue

        # Check existing user
        existing_user = User.objects.filter(username__iexact=staff_id).first()
        if existing_user:
            if is_system_protected_account(existing_user):
                results["system_skipped"] += 1
                continue
            if hasattr(existing_user, 'assignment') and existing_user.assignment.personnel and existing_user.assignment.personnel != personnel:
                results["existing_skipped"] += 1
                continue

        if dry_run:
            results["created"] += 1
            continue

        try:
            _, created, msg = provision_personnel_account(
                personnel=personnel,
                temporary_password=temporary_password
            )
            if created:
                results["created"] += 1
            else:
                results["already_linked"] += 1
        except Exception as e:
            results["errors"] += 1
            results["details"].append(f"Error provisioning {staff_id}: {str(e)}")

    return results


def reset_personnel_account(personnel, requested_by, temporary_password=None):
    """
    Reset account access for a personnel-linked user.
    Sets password back to the configured temporary password, sets must_change_password=True,
    invalidates existing sessions, and preserves all other data (roles, posting, leave, transfers).
    Strictly protects system super admin from reset.
    """
    if not personnel:
        raise ValidationError("Personnel record must be specified.")

    user = personnel.user_account
    if not user:
        raise ValidationError(f"No user account exists for personnel {personnel.fullname} ({personnel.staffID}).")

    # Protection: Never allow resetting system super admin through personnel admin
    if is_system_protected_account(user):
        raise PermissionDenied("Protected system bootstrap accounts cannot be reset via personnel administration.")

    temp_pw = temporary_password or get_temporary_password()

    with transaction.atomic():
        user.set_password(temp_pw)  # Django set_password automatically updates session auth hash, invalidating prior sessions
        user.save()

        assignment, _ = UserAssignment.objects.get_or_create(
            user=user,
            defaults={"personnel": personnel, "role": GNFSUserRole.STAFF, "active": True}
        )
        assignment.must_change_password = True
        assignment.password_changed_at = None
        assignment.save(update_fields=['must_change_password', 'password_changed_at', 'updated_at'])

        AuditLog.objects.create(
            user=requested_by,
            action="ACCOUNT_ACCESS_RESET",
            target_type="User",
            target_id=str(user.id),
            description=f"Account access for {personnel.fullname} (Staff ID: {personnel.staffID}) was reset to the temporary credential by {requested_by.username}. Mandatory password change required.",
            station=personnel.station,
            district=personnel.current_district,
            region=personnel.current_region
        )

        send_notification(
            recipient=user,
            title="Account Access Reset",
            message="Your account password has been reset by an administrator. You will be required to create a new private password upon your next login.",
            notification_type="WARNING"
        )

    return user
