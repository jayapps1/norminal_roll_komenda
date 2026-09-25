import getpass
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create or update an application login or admin account."

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            default=os.getenv("APP_ADMIN_USERNAME", "admin"),
            help="Application login username (default: APP_ADMIN_USERNAME or admin).",
        )
        parser.add_argument(
            "--email",
            default=os.getenv("APP_ADMIN_EMAIL", ""),
            help="Optional application account email (default: APP_ADMIN_EMAIL).",
        )
        parser.add_argument(
            "--password",
            default=None,
            help="Application login password. Prefer APP_ADMIN_PASSWORD or the interactive prompt.",
        )
        parser.add_argument(
            "--reset-password",
            action="store_true",
            help="Reset the password for an existing account.",
        )
        parser.add_argument(
            "--superuser",
            action="store_true",
            help="Grant superuser and staff permissions (Django admin access).",
        )
        parser.add_argument(
            "--is-staff",
            action="store_true",
            help="Grant staff permissions.",
        )

    def handle(self, *args, **options):
        username = options["username"].strip()
        if not username:
            raise CommandError("Username cannot be empty.")

        user_model = get_user_model()
        password = options["password"] or os.getenv("APP_ADMIN_PASSWORD")
        user = user_model.objects.filter(username=username).first()
        created = user is None

        if created or options["reset_password"]:
            password = password or getpass.getpass("Admin password: ")
            if not password:
                raise CommandError("Password cannot be empty.")

        if created:
            user = user_model(username=username)
            user.set_password(password)
            user.is_staff = bool(options.get("superuser") or options.get("is_staff"))
            user.is_superuser = bool(options.get("superuser"))
        else:
            if options["reset_password"]:
                user.set_password(password)
            if options.get("superuser"):
                user.is_staff = True
                user.is_superuser = True
            elif options.get("is_staff"):
                user.is_staff = True

        email = options["email"].strip() if options["email"] else ""
        if not email and not getattr(user, "email", "") and "@" in username:
            email = username
        if email:
            user.email = email

        user.is_active = True
        user.save()

        # Synchronize GNFS National Admin role assignment for superusers
        if user.is_superuser:
            from staff.models import UserAssignment, GNFSUserRole
            UserAssignment.objects.update_or_create(
                user=user,
                defaults={"role": GNFSUserRole.NATIONAL_ADMIN, "active": True}
            )

        account_type = "superuser" if user.is_superuser else ("staff" if user.is_staff else "application")
        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} {account_type} login account '{username}'."))

