import base64
from io import BytesIO
import secrets
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

try:
    import pyotp
except ImportError:
    pyotp = None

try:
    import qrcode
    import qrcode.image.svg
except ImportError:
    qrcode = None


def generate_random_base32():
    if pyotp is not None:
        return pyotp.random_base32()
    # RFC 3548 Base32 using Python standard library
    random_bytes = secrets.token_bytes(20)
    return base64.b32encode(random_bytes).decode("ascii").replace("=", "")


class StaffTOTPDevice(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="totp_device",
    )
    secret = models.CharField(max_length=64, default=generate_random_base32)
    is_confirmed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"TOTP Device ({self.user.username}) - {'Confirmed' if self.is_confirmed else 'Pending'}"

    def get_provisioning_uri(self):
        label = self.user.email or self.user.username
        if pyotp is not None:
            return pyotp.TOTP(self.secret).provisioning_uri(
                name=label,
                issuer_name="GNFS Personnel Roll",
            )
        import urllib.parse
        clean_label = urllib.parse.quote(f"GNFS Personnel Roll:{label}")
        issuer = urllib.parse.quote("GNFS Personnel Roll")
        return f"otpauth://totp/{clean_label}?secret={self.secret}&issuer={issuer}"

    def get_qr_code_svg(self):
        """Generate inline SVG string for QR code without Pillow dependency."""
        if qrcode is None:
            return None
        uri = self.get_provisioning_uri()
        factory = qrcode.image.svg.SvgPathImage
        img = qrcode.make(uri, image_factory=factory)
        stream = BytesIO()
        img.save(stream)
        return stream.getvalue().decode("utf-8")

    def verify_token(self, token):
        if not token or not self.secret:
            return False
        clean_token = str(token).strip().replace(" ", "")
        if pyotp is not None:
            return bool(pyotp.TOTP(self.secret).verify(clean_token, valid_window=1))

        # Standard library RFC 6238 TOTP verification fallback
        import hashlib
        import hmac
        import struct
        import time

        secret = self.secret.strip().replace(" ", "").upper()
        padding = (8 - (len(secret) % 8)) % 8
        secret += "=" * padding
        try:
            key = base64.b32decode(secret, casefold=True)
        except Exception:
            return False

        current_time = time.time()
        for offset_window in (-1, 0, 1):
            counter = int((current_time + (offset_window * 30)) // 30)
            msg = struct.pack(">Q", counter)
            digest = hmac.new(key, msg, hashlib.sha1).digest()
            offset = digest[-1] & 0x0F
            code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1000000
            expected_token = str(code).zfill(6)
            if hmac.compare_digest(clean_token, expected_token):
                return True
        return False


# ==============================================================================
# GNFS ROLE & USER ASSIGNMENT ARCHITECTURE
# ==============================================================================

class GNFSUserRole(models.TextChoices):
    NATIONAL_ADMIN = "NATIONAL_ADMIN", "National Administrator"
    REGIONAL_ADMIN = "REGIONAL_ADMIN", "Regional Administrator"
    DFO = "DFO", "District Fire Officer (DFO)"
    DISTRICT_ADMIN = "DISTRICT_ADMIN", "District Administrator"
    STATION_ADMIN = "STATION_ADMIN", "Station Administrator"
    POST_ADMIN = "POST_ADMIN", "Post Administrator"
    STAFF = "STAFF", "Station Staff"


class UserAssignment(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="assignment"
    )
    role = models.CharField(
        max_length=30,
        choices=GNFSUserRole.choices,
        default=GNFSUserRole.STAFF,
        db_index=True
    )
    personnel = models.OneToOneField(
        'gnfs_komenda.NorminalRoll',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="user_assignment"
    )
    must_change_password = models.BooleanField(
        default=False,
        help_text="Require user to change their password on next login."
    )
    is_system_account = models.BooleanField(
        default=False,
        help_text="True if this is a bootstrap/technical system admin account."
    )
    password_changed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of when user last changed their password."
    )
    region = models.ForeignKey(
        'gnfs_komenda.Region',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="user_assignments"
    )
    district = models.ForeignKey(
        'gnfs_komenda.District',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="user_assignments"
    )
    station = models.ForeignKey(
        'gnfs_komenda.Station',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="user_assignments"
    )
    post = models.ForeignKey(
        'gnfs_komenda.Post',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="user_assignments"
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User Assignment"
        verbose_name_plural = "User Assignments"

    @property
    def account_status(self):
        """Human-readable account status summary."""
        if not self.user.is_active:
            return "INACTIVE / LOCKED"
        if self.must_change_password:
            return "CHANGE REQUIRED"
        return "ACTIVE"

    @property
    def totp_configured(self):
        """Return True if 2FA (TOTP) is active and confirmed."""
        totp = getattr(self.user, 'totp_device', None)
        return bool(totp and totp.is_confirmed)

    def clean(self):
        super().clean()
        # Coherence rules & automatic parent hierarchy resolution
        if self.role == GNFSUserRole.NATIONAL_ADMIN:
            self.region = None
            self.district = None
            self.station = None
            self.post = None

        elif self.role == GNFSUserRole.REGIONAL_ADMIN:
            if not self.region:
                raise ValidationError({"region": "Regional Administrator requires an assigned Region."})
            self.district = None
            self.station = None
            self.post = None

        elif self.role in (GNFSUserRole.DFO, GNFSUserRole.DISTRICT_ADMIN):
            if not self.district:
                raise ValidationError({"district": "District role requires an assigned District."})
            self.region = self.district.region
            self.station = None
            self.post = None

        elif self.role == GNFSUserRole.STATION_ADMIN:
            if not self.station:
                raise ValidationError({"station": "Station Administrator requires an assigned Fire Station."})
            self.district = self.station.district
            self.region = self.station.district.region
            self.post = None

        elif self.role == GNFSUserRole.POST_ADMIN:
            if not self.post:
                raise ValidationError({"post": "Post Administrator requires an assigned Post."})
            self.station = self.post.station
            self.district = self.post.station.district
            self.region = self.post.station.district.region

        elif self.role == GNFSUserRole.STAFF:
            # Sync parents if station or post is specified
            if self.post:
                self.station = self.post.station
                self.district = self.post.station.district
                self.region = self.post.station.district.region
            elif self.station:
                self.district = self.station.district
                self.region = self.station.district.region
            elif self.district:
                self.region = self.district.region

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        scope_str = "Nationwide"
        if self.post:
            scope_str = f"Post: {self.post.name}"
        elif self.station:
            scope_str = f"Station: {self.station.name}"
        elif self.district:
            scope_str = f"District: {self.district.name}"
        elif self.region:
            scope_str = f"Region: {self.region.name}"
        return f"{self.user.username} - {self.get_role_display()} ({scope_str})"

    @property
    def is_national_admin(self):
        return self.role == GNFSUserRole.NATIONAL_ADMIN or self.user.is_superuser

    @property
    def is_regional_admin(self):
        return self.role == GNFSUserRole.REGIONAL_ADMIN

    @property
    def is_dfo(self):
        return self.role == GNFSUserRole.DFO

    @property
    def is_district_admin(self):
        return self.role in (GNFSUserRole.DFO, GNFSUserRole.DISTRICT_ADMIN)

    @property
    def is_station_admin(self):
        return self.role == GNFSUserRole.STATION_ADMIN

    @property
    def is_post_admin(self):
        return self.role == GNFSUserRole.POST_ADMIN
