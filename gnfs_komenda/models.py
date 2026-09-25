from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

payroll_status_choice = [
    ('ACTIVE', 'ACTIVE'),
    ('NON ACTIVE', 'NON ACTIVE')
]

leave_status_choice = [
    ('AT POST', 'AT POST'),
    ('ON LEAVE', 'ON LEAVE')
]

leave_type_choice = [
    ('AT POST', 'AT POST'),
    ('ANNUAL LEAVE', 'ANNUAL LEAVE'),
    ('THERMINAL LEAVE', 'THERMINAL LEAVE'),
    ('MATERNITY LEAVE', 'MATERNITY LEAVE'),
    ('STUDY LEAVE', 'STUDY LEAVE'),
    ('SICK LEAVE', 'SICK LEAVE'),
]

# ==============================================================================
# GNFS ORGANISATIONAL HIERARCHY MODELS
# ==============================================================================

class Region(models.Model):
    name = models.CharField(max_length=100, unique=True, db_index=True)
    code = models.CharField(max_length=20, unique=True, db_index=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = "Region"
        verbose_name_plural = "Regions"

    def __str__(self):
        return f"{self.name} ({self.code})"


class District(models.Model):
    region = models.ForeignKey(
        Region,
        on_delete=models.PROTECT,
        related_name="districts",
        db_index=True
    )
    name = models.CharField(max_length=150, db_index=True)
    code = models.CharField(max_length=50, blank=True, default='')
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['region__name', 'name']
        constraints = [
            models.UniqueConstraint(fields=['region', 'name'], name='unique_district_per_region'),
        ]
        verbose_name = "District"
        verbose_name_plural = "Districts"

    def __str__(self):
        return f"{self.name} ({self.region.code})"


class Station(models.Model):
    STATION_TYPE_CHOICES = [
        ('MUNICIPAL', 'Municipal Fire Station'),
        ('DISTRICT', 'District Fire Station'),
        ('METRO', 'Metropolitan Fire Station'),
        ('INSTITUTIONAL', 'Institutional Fire Station'),
        ('REGIONAL_HQ', 'Regional Headquarters Station'),
        ('NATIONAL_HQ', 'National Headquarters Station'),
        ('SUB_STATION', 'Sub-Station'),
        ('SPECIAL', 'Special Operational Station'),
    ]

    district = models.ForeignKey(
        District,
        on_delete=models.PROTECT,
        related_name="stations",
        db_index=True
    )
    name = models.CharField(max_length=150, db_index=True)
    code = models.CharField(max_length=50, blank=True, default='')
    station_type = models.CharField(
        max_length=50,
        choices=STATION_TYPE_CHOICES,
        default='DISTRICT'
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['district__name', 'name']
        constraints = [
            models.UniqueConstraint(fields=['district', 'name'], name='unique_station_per_district'),
        ]
        verbose_name = "Fire Station"
        verbose_name_plural = "Fire Stations"

    def __str__(self):
        return f"{self.name} - {self.district.name}"


class Post(models.Model):
    POST_TYPE_CHOICES = [
        ('MARKET_POST', 'Market Fire Post'),
        ('SUB_POST', 'Sub-Post'),
        ('CHECKPOINT', 'Fire Checkpoint'),
        ('COMMUNITY_POST', 'Community Post'),
    ]

    station = models.ForeignKey(
        Station,
        on_delete=models.PROTECT,
        related_name="posts",
        db_index=True
    )
    name = models.CharField(max_length=150, db_index=True)
    code = models.CharField(max_length=50, blank=True, default='')
    post_type = models.CharField(
        max_length=50,
        choices=POST_TYPE_CHOICES,
        default='MARKET_POST'
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['station__name', 'name']
        constraints = [
            models.UniqueConstraint(fields=['station', 'name'], name='unique_post_per_station'),
        ]
        verbose_name = "Market Post / Sub-Post"
        verbose_name_plural = "Market Posts / Sub-Posts"

    def __str__(self):
        return f"{self.name} ({self.station.name})"


# ==============================================================================
# GNFS RANK / JOB GRADE MODEL
# ==============================================================================

class Rank(models.Model):
    class OfficerCategory(models.TextChoices):
        SENIOR = "SENIOR", "Senior Officer"
        JUNIOR = "JUNIOR", "Junior Officer"

    name = models.CharField(max_length=150, unique=True, db_index=True)
    abbreviation = models.CharField(max_length=30, db_index=True)
    officer_category = models.CharField(
        max_length=10,
        choices=OfficerCategory.choices,
        db_index=True
    )
    hierarchy_order = models.PositiveIntegerField(unique=True, db_index=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["hierarchy_order"]
        verbose_name = "Rank"
        verbose_name_plural = "Ranks"

    def __str__(self):
        return f"{self.name} ({self.abbreviation})"


# ==============================================================================
# PERSONNEL MODEL (NORMINAL ROLL)
# ==============================================================================

class ActivePersonnelManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)


class NorminalRoll(models.Model):
    # Organisational Hierarchy Linkage
    station = models.ForeignKey(
        Station,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="personnel",
        db_index=True
    )
    post = models.ForeignKey(
        Post,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="personnel",
        db_index=True
    )

    # Rank & Officer Category Structure
    officer_category = models.CharField(
        max_length=10,
        choices=Rank.OfficerCategory.choices,
        blank=True,
        default='',
        db_index=True
    )
    rank = models.ForeignKey(
        Rank,
        on_delete=models.PROTECT,
        related_name="personnel",
        null=True,
        blank=True,
        db_index=True
    )
    job_grade = models.CharField(max_length=50, blank=True, default='')

    class Gender(models.TextChoices):
        MALE = "MALE", "Male"
        FEMALE = "FEMALE", "Female"

    gender = models.CharField(
        max_length=10,
        choices=Gender.choices,
        default=Gender.MALE,
        db_index=True
    )

    staffID = models.CharField(
        max_length=10,
        validators=[
            RegexValidator(r'^\d{5,10}$', 'Staff ID must be exactly 7 digits.')
        ],
        unique=True
    )
    fullname = models.CharField(max_length=255, db_index=True)
    birth_date = models.DateField()
    ghana_card = models.CharField(
        max_length=15,
        validators=[
            RegexValidator(r'^GHA-\d{9}-\d$', 'Enter a valid Ghana Card number (e.g., GHA-123456789-0).')
        ],
        unique=True
    )
    mgmt_units = models.CharField(max_length=150)
    bank = models.CharField(max_length=50)
    account_number = models.CharField(
        max_length=20,
        validators=[RegexValidator(r'^[0-9-]+$', 'Enter a valid bank account number.')]
    )
    payroll_status = models.CharField(max_length=30, choices=payroll_status_choice, default='ACTIVE')
    leave_status = models.CharField(max_length=30, choices=leave_status_choice, default='AT POST')
    leave_type = models.CharField(max_length=30, choices=leave_type_choice, default='ANNUAL LEAVE', null=True, blank=True)
    start_leave = models.DateField(blank=True, null=True)
    end_leave = models.DateField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    called_off = models.BooleanField(default=False)
    manual_override = models.BooleanField(default=False)

    # Soft Delete & Archival Fields
    is_active = models.BooleanField(default=True, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="archived_personnel"
    )
    archive_reason = models.TextField(blank=True, default='')

    # Managers
    objects = models.Manager()
    active_objects = ActivePersonnelManager()

    class Meta:
        indexes = [
            models.Index(fields=['is_active', 'leave_status']),
            models.Index(fields=['fullname']),
        ]
        verbose_name = "Personnel Record"
        verbose_name_plural = "Personnel Records"

    @classmethod
    def sync_leaves(cls):
        """Automatically synchronize leave_status based on current date for non-overridden staff."""
        today = timezone.now().date()
        # Reset exhausted leaves to AT POST
        cls.objects.filter(
            end_leave__lt=today,
            leave_status="ON LEAVE",
            manual_override=False,
            called_off=False,
        ).update(leave_status="AT POST")

        # Activate active ongoing leaves
        cls.objects.filter(
            start_leave__lte=today,
            end_leave__gte=today,
            leave_status="AT POST",
            manual_override=False,
            called_off=False,
        ).update(leave_status="ON LEAVE")

    def clean(self):
        super().clean()
        if self.rank and self.officer_category and self.rank.officer_category != self.officer_category:
            from django.core.exceptions import ValidationError
            raise ValidationError({'rank': f"Rank '{self.rank.name}' does not match the selected officer category."})

    def save(self, *args, **kwargs):
        """Apply rank and leave rules: called_off, expired leave, or manual override."""
        today = timezone.now().date()

        # Synchronize rank, officer category, and job grade
        if self.rank:
            self.officer_category = self.rank.officer_category
            if not self.job_grade or self.job_grade != self.rank.abbreviation:
                self.job_grade = self.rank.abbreviation

        if self.called_off:
            self.leave_type = "AT POST"
            self.leave_status = "AT POST"
            self.start_leave = None
            self.end_leave = None

        elif self.manual_override:
            if not self.leave_status:
                self.leave_status = "AT POST"

        elif self.start_leave and self.end_leave:
            if self.start_leave <= today <= self.end_leave:
                self.leave_status = "ON LEAVE"
            else:
                self.leave_status = "AT POST"

        else:
            if not self.leave_status:
                self.leave_status = "AT POST"

        super().save(*args, **kwargs)

    @property
    def rank_display(self):
        if self.rank:
            return f"{self.rank.name} ({self.rank.abbreviation})"
        return self.job_grade or "Unassigned"

    @property
    def rank_short(self):
        if self.rank:
            return self.rank.abbreviation
        return self.job_grade or "—"

    def __str__(self):
        return f"{self.staffID} - {self.fullname}"

    @property
    def current_region(self):
        if self.station and self.station.district and self.station.district.region:
            return self.station.district.region
        return None

    @property
    def current_district(self):
        if self.station and self.station.district:
            return self.station.district
        return None


# ==============================================================================
# APPROVAL WORKFLOW & AUDIT TRAIL MODELS
# ==============================================================================

class PersonnelActionRequest(models.Model):
    ACTION_CHOICES = [
        ('ARCHIVE', 'Archive Personnel'),
        ('RESTORE', 'Restore Personnel'),
    ]

    STATUS_CHOICES = [
        ('PENDING_DFO', 'Pending DFO Approval'),
        ('PENDING_REGIONAL', 'Pending Regional Approval'),
        ('PENDING_NATIONAL', 'Pending National Approval'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
        ('CANCELLED', 'Cancelled'),
    ]

    LEVEL_CHOICES = [
        ('DFO', 'District Fire Officer (DFO)'),
        ('REGIONAL', 'Regional Administrator'),
        ('NATIONAL', 'National Administrator'),
        ('COMPLETED', 'Completed'),
    ]

    personnel = models.ForeignKey(
        NorminalRoll,
        on_delete=models.CASCADE,
        related_name="action_requests"
    )
    action_type = models.CharField(max_length=20, choices=ACTION_CHOICES, default='ARCHIVE')
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="requested_actions"
    )
    reason = models.TextField()
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='PENDING_DFO', db_index=True)
    current_approval_level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default='DFO')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Personnel Action Request"
        verbose_name_plural = "Personnel Action Requests"

    def __str__(self):
        return f"{self.get_action_type_display()} for {self.personnel.fullname} ({self.get_status_display()})"


class ApprovalHistory(models.Model):
    DECISION_CHOICES = [
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    ]

    LEVEL_CHOICES = [
        ('DFO', 'District Fire Officer (DFO)'),
        ('REGIONAL', 'Regional Administrator'),
        ('NATIONAL', 'National Administrator'),
    ]

    request = models.ForeignKey(
        PersonnelActionRequest,
        on_delete=models.CASCADE,
        related_name="history"
    )
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="approvals_given"
    )
    approval_level = models.CharField(max_length=20, choices=LEVEL_CHOICES)
    decision = models.CharField(max_length=20, choices=DECISION_CHOICES)
    comment = models.TextField(blank=True, default='')
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['timestamp']
        verbose_name = "Approval History"
        verbose_name_plural = "Approval Histories"

    def __str__(self):
        return f"{self.approval_level} {self.decision} by {self.approver.username} on #{self.request.id}"


class AuditLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs"
    )
    action = models.CharField(max_length=100, db_index=True)
    target_type = models.CharField(max_length=50)
    target_id = models.CharField(max_length=50)
    description = models.TextField()
    old_values = models.JSONField(null=True, blank=True)
    new_values = models.JSONField(null=True, blank=True)

    # Scoping context for fast regional/district audit filtering
    region = models.ForeignKey(Region, on_delete=models.SET_NULL, null=True, blank=True)
    district = models.ForeignKey(District, on_delete=models.SET_NULL, null=True, blank=True)
    station = models.ForeignKey(Station, on_delete=models.SET_NULL, null=True, blank=True)
    post = models.ForeignKey(Post, on_delete=models.SET_NULL, null=True, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-timestamp']
        verbose_name = "Audit Log"
        verbose_name_plural = "Audit Logs"

    def __str__(self):
        return f"[{self.timestamp.strftime('%Y-%m-%d %H:%M')}] {self.action} by {self.user.username if self.user else 'System'}"


class Notification(models.Model):
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications"
    )
    title = models.CharField(max_length=255)
    message = models.TextField()
    notification_type = models.CharField(max_length=50, default='INFO')
    link_url = models.CharField(max_length=255, blank=True, default='')
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Notification"
        verbose_name_plural = "Notifications"

    def __str__(self):
        return f"To {self.recipient.username}: {self.title}"


# ==============================================================================
# ENTERPRISE LEAVE MANAGEMENT MODELS
# ==============================================================================

class LeaveType(models.Model):
    class CalculationMode(models.TextChoices):
        WORKING_DAYS = "WORKING_DAYS", "Working Days (Excludes Weekends & Public Holidays)"
        CALENDAR_DAYS = "CALENDAR_DAYS", "Calendar Days (Includes Weekends & Holidays)"
        DATE_RANGE = "DATE_RANGE", "Date Range (Medical / Study Direction)"
        CALENDAR_MONTHS = "CALENDAR_MONTHS", "Calendar Months"

    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=30, unique=True, db_index=True)
    calculation_mode = models.CharField(
        max_length=20,
        choices=CalculationMode.choices,
        default=CalculationMode.WORKING_DAYS
    )
    default_days_junior = models.PositiveIntegerField(default=0, help_text="Entitlement for Junior Officers in days")
    default_days_senior = models.PositiveIntegerField(default=0, help_text="Entitlement for Senior Officers in days")
    calendar_months = models.PositiveIntegerField(default=0, help_text="Duration in calendar months if CALENDAR_MONTHS mode")
    class AllowedGender(models.TextChoices):
        ALL = "ALL", "All Personnel"
        MALE = "MALE", "Male Only"
        FEMALE = "FEMALE", "Female Only"

    allowed_gender = models.CharField(
        max_length=10,
        choices=AllowedGender.choices,
        default=AllowedGender.ALL,
        db_index=True,
        help_text="Gender restriction for this leave type"
    )
    is_deferrable = models.BooleanField(default=False, help_text="Can unused balance be deferred to next year?")
    requires_attachment = models.BooleanField(default=False, help_text="Requires doctor's note, memo, or supporting doc")
    description = models.TextField(blank=True, default="")
    active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = "Leave Type"
        verbose_name_plural = "Leave Types"

    def __str__(self):
        return self.name

    def get_entitlement_for_category(self, officer_category):
        if officer_category == Rank.OfficerCategory.SENIOR:
            return self.default_days_senior
        return self.default_days_junior

    @property
    def requires_entitlement(self):
        return self.code in ("ANNUAL", "CASUAL") or (self.default_days_junior > 0 or self.default_days_senior > 0)

    @property
    def default_days(self):
        return self.default_days_junior


class PublicHoliday(models.Model):
    name = models.CharField(max_length=150)
    date = models.DateField(unique=True, db_index=True)
    year = models.PositiveIntegerField(db_index=True)
    active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date']
        verbose_name = "Public Holiday"
        verbose_name_plural = "Public Holidays"

    def save(self, *args, **kwargs):
        if self.date:
            self.year = self.date.year
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.date.strftime('%b %d, %Y')})"


class LeaveEntitlement(models.Model):
    personnel = models.ForeignKey(
        NorminalRoll,
        on_delete=models.CASCADE,
        related_name="leave_entitlements",
        db_index=True
    )
    leave_year = models.PositiveIntegerField(db_index=True)
    leave_type = models.ForeignKey(
        LeaveType,
        on_delete=models.PROTECT,
        related_name="entitlements"
    )
    base_entitlement = models.PositiveIntegerField(default=0)
    carried_forward = models.PositiveIntegerField(default=0, help_text="Approved deferred days carried from previous year")
    adjustments = models.IntegerField(default=0, help_text="Manual authorized adjustments")
    days_taken = models.PositiveIntegerField(default=0, help_text="Total approved and consumed days")
    days_reserved = models.PositiveIntegerField(default=0, help_text="Days locked in pending requests")
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-leave_year', 'leave_type__name']
        unique_together = [('personnel', 'leave_year', 'leave_type')]
        verbose_name = "Leave Entitlement"
        verbose_name_plural = "Leave Entitlements"

    @property
    def total_available(self):
        return max(0, self.base_entitlement + self.carried_forward + self.adjustments)

    @property
    def remaining_balance(self):
        return max(0, self.total_available - self.days_taken - self.days_reserved)

    def __str__(self):
        return f"{self.personnel.fullname} - {self.leave_type.name} ({self.leave_year}): {self.remaining_balance} left"


class LeaveRequest(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        PENDING_APPROVAL = "PENDING_APPROVAL", "Pending Approval"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        CANCELLED = "CANCELLED", "Cancelled"
        ACTIVE = "ACTIVE", "Active / On Leave"
        COMPLETED = "COMPLETED", "Completed / Returned to Post"
        RECALLED = "RECALLED", "Recalled / Called Off"

    personnel = models.ForeignKey(
        NorminalRoll,
        on_delete=models.CASCADE,
        related_name="leave_requests",
        db_index=True
    )
    entitlement = models.ForeignKey(
        LeaveEntitlement,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="requests"
    )
    leave_type = models.ForeignKey(
        LeaveType,
        on_delete=models.PROTECT,
        related_name="requests"
    )
    leave_year = models.PositiveIntegerField(db_index=True)
    officer_category_snapshot = models.CharField(max_length=10, choices=Rank.OfficerCategory.choices)
    rank_snapshot = models.ForeignKey(
        Rank,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="historical_leave_requests"
    )

    start_date = models.DateField(db_index=True)
    duration = models.PositiveIntegerField(help_text="Duration in days (or calendar months for Terminal Leave)")
    is_proportionate = models.BooleanField(default=False, help_text="Proportionate / Part Annual Leave")
    calculation_mode = models.CharField(max_length=20, choices=LeaveType.CalculationMode.choices)
    last_leave_date = models.DateField(db_index=True)
    resume_date = models.DateField(db_index=True)
    working_days_count = models.PositiveIntegerField(default=0)
    calendar_days_count = models.PositiveIntegerField(default=0)

    reason = models.TextField(blank=True, default="")
    contact_phone = models.CharField(max_length=50, blank=True, default="")
    contact_address = models.CharField(max_length=255, blank=True, default="")
    handover_notes = models.TextField(blank=True, default="")
    medical_reference = models.CharField(max_length=150, blank=True, default="", help_text="Medical facility / doctor reference")
    attachment = models.FileField(upload_to="leave_attachments/", null=True, blank=True)

    status = models.CharField(
        max_length=25,
        choices=Status.choices,
        default=Status.PENDING_APPROVAL,
        db_index=True
    )

    # Workflow Audit
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="leave_requests_made"
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="leave_requests_approved"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default="")

    # Recall / Call-off tracking
    is_recalled = models.BooleanField(default=False)
    recall_date = models.DateField(null=True, blank=True)
    actual_days_consumed = models.PositiveIntegerField(null=True, blank=True, help_text="Working days consumed before recall")
    days_restored = models.PositiveIntegerField(default=0, help_text="Days restored back to entitlement on recall")
    recall_reason = models.TextField(blank=True, default="")
    recalled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="leave_recalls_authorized"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if self.personnel:
            if not self.officer_category_snapshot:
                self.officer_category_snapshot = self.personnel.officer_category
            if not self.rank_snapshot:
                self.rank_snapshot = self.personnel.rank
        if not self.leave_year and self.start_date:
            self.leave_year = self.start_date.year
        super().save(*args, **kwargs)

    class Meta:
        ordering = ['-start_date', '-created_at']
        verbose_name = "Leave Request"
        verbose_name_plural = "Leave Requests"

    def __str__(self):
        return f"{self.personnel.fullname} - {self.leave_type.name} ({self.start_date} to {self.last_leave_date}) [{self.get_status_display()}]"



class LeaveApprovalDecision(models.Model):
    DECISION_CHOICES = [
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    ]

    leave_request = models.ForeignKey(
        LeaveRequest,
        on_delete=models.CASCADE,
        related_name="decisions"
    )
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="leave_decisions_given"
    )
    approver_role = models.CharField(max_length=50)
    decision = models.CharField(max_length=20, choices=DECISION_CHOICES)
    comments = models.TextField(blank=True, default="")
    previous_status = models.CharField(max_length=30)
    new_status = models.CharField(max_length=30)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['timestamp']
        verbose_name = "Leave Approval Decision"
        verbose_name_plural = "Leave Approval Decisions"

    def __str__(self):
        return f"{self.decision} by {self.approver.username} on Leave #{self.leave_request_id}"


class LeaveDefermentRequest(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        PENDING = "PENDING", "Pending Approval"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        CANCELLED = "CANCELLED", "Cancelled"

    personnel = models.ForeignKey(
        NorminalRoll,
        on_delete=models.CASCADE,
        related_name="deferment_requests",
        db_index=True
    )
    leave_year = models.PositiveIntegerField(db_index=True, help_text="Year from which leave is being deferred")
    days_requested = models.PositiveIntegerField(help_text="Number of remaining leave days requested for deferment")
    reason = models.TextField(help_text="Operational justification for deferment")
    letter_reference = models.CharField(max_length=150, blank=True, default="", help_text="Official memo/letter reference")
    attachment = models.FileField(upload_to="deferment_letters/", null=True, blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="deferments_requested"
    )
    requested_at = models.DateTimeField(auto_now_add=True)

    approved_days = models.PositiveIntegerField(default=0, help_text="Number of days approved for carry-forward")
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="deferments_approved"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    remarks = models.TextField(blank=True, default="")
    carried_into_year = models.PositiveIntegerField(null=True, blank=True, help_text="Target year into which balance was carried")
    is_carried_forward = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def target_year(self):
        return self.carried_into_year or (self.leave_year + 1)

    @property
    def carried_forward_applied(self):
        return self.is_carried_forward

    @property
    def approval_remarks(self):
        return self.remarks

    class Meta:
        ordering = ['-requested_at']
        verbose_name = "Leave Deferment Request"
        verbose_name_plural = "Leave Deferment Requests"

    def __str__(self):
        return f"{self.personnel.fullname} - {self.days_requested} days deferred from {self.leave_year} [{self.get_status_display()}]"


class LeaveAdjustmentAudit(models.Model):
    entitlement = models.ForeignKey(
        LeaveEntitlement,
        on_delete=models.CASCADE,
        related_name="adjustment_audits"
    )
    adjusted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="leave_adjustments_authorized"
    )
    days_adjusted = models.IntegerField(help_text="Positive for additions, negative for deductions")
    previous_balance = models.IntegerField()
    new_balance = models.IntegerField()
    reason = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']
        verbose_name = "Leave Adjustment Audit"
        verbose_name_plural = "Leave Adjustment Audits"

    def __str__(self):
        return f"{self.entitlement.personnel.fullname} {self.days_adjusted:+d} days by {self.adjusted_by.username}"
