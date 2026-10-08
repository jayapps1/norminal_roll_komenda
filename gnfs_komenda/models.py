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

    @property
    def current_rfo(self):
        """Active Regional Fire Officer appointment."""
        return self.command_appointments.filter(
            appointment_type__in=['RFO', 'ACTING_RFO'],
            is_active=True
        ).select_related('personnel', 'personnel__rank').first()

    @property
    def current_2ic(self):
        """Active Regional Second-in-Command appointment."""
        return self.command_appointments.filter(
            appointment_type__in=['REGIONAL_2IC', 'ACTING_REGIONAL_2IC'],
            is_active=True
        ).select_related('personnel', 'personnel__rank').first()


class District(models.Model):
    class CommandType(models.TextChoices):
        DISTRICT = "DISTRICT", "District Fire Command"
        MUNICIPAL = "MUNICIPAL", "Municipal Fire Command"
        METROPOLITAN = "METROPOLITAN", "Metropolitan Fire Command"

    class CommandingOfficerDesignation(models.TextChoices):
        DFO = "DFO", "District Fire Officer (DFO)"
        MFO = "MFO", "Municipal Fire Officer (MFO)"
        METRO_FO = "METRO_FO", "Metropolitan Fire Officer (MFO)"

    region = models.ForeignKey(
        Region,
        on_delete=models.PROTECT,
        related_name="districts",
        db_index=True
    )
    name = models.CharField(max_length=150, db_index=True)
    code = models.CharField(max_length=50, blank=True, default='')
    command_type = models.CharField(
        max_length=30,
        choices=CommandType.choices,
        default=CommandType.DISTRICT,
        db_index=True
    )
    co_designation = models.CharField(
        max_length=30,
        choices=CommandingOfficerDesignation.choices,
        default=CommandingOfficerDesignation.DFO
    )
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

    @property
    def current_commanding_officer(self):
        """Active DFO / MFO appointment."""
        return self.command_appointments.filter(
            appointment_type__in=['DFO', 'MFO', 'METRO_FO', 'ACTING_DFO', 'ACTING_MFO', 'ACTING_METRO_FO'],
            is_active=True
        ).select_related('personnel', 'personnel__rank').first()

    @property
    def current_2ic(self):
        """Active District Second-in-Command appointment."""
        return self.command_appointments.filter(
            appointment_type__in=['DISTRICT_2IC', 'ACTING_DISTRICT_2IC'],
            is_active=True
        ).select_related('personnel', 'personnel__rank').first()


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
# GNFS FUNCTIONAL ORGANISATIONAL STRUCTURE MODELS
# ==============================================================================

class FunctionalUnit(models.Model):
    class FunctionalUnitType(models.TextChoices):
        DIRECTORATE = "DIRECTORATE", "Directorate"
        DIVISION = "DIVISION", "Division"
        DEPARTMENT = "DEPARTMENT", "Department"
        UNIT = "UNIT", "Unit"
        SECTION = "SECTION", "Section"

    name = models.CharField(max_length=150, db_index=True)
    code = models.CharField(max_length=30, blank=True, default='', db_index=True)
    unit_type = models.CharField(
        max_length=20,
        choices=FunctionalUnitType.choices,
        default=FunctionalUnitType.DIRECTORATE,
        db_index=True
    )
    parent = models.ForeignKey(
        'self',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="children",
        db_index=True
    )
    description = models.TextField(blank=True, default='')
    active = models.BooleanField(default=True, db_index=True)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['display_order', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['parent', 'name'],
                name='unique_functional_unit_per_parent',
                condition=models.Q(parent__isnull=False)
            ),
            models.UniqueConstraint(
                fields=['name'],
                name='unique_top_level_directorate',
                condition=models.Q(parent__isnull=True)
            ),
        ]
        verbose_name = "Functional Organisational Unit"
        verbose_name_plural = "Functional Organisational Units"

    def __str__(self):
        return self.get_full_path()

    def get_ancestors(self):
        """Return list of ancestor units starting from top Directorate down to parent."""
        ancestors = []
        curr = self.parent
        visited = set()
        while curr and curr.id not in visited:
            visited.add(curr.id)
            ancestors.insert(0, curr)
            curr = curr.parent
        return ancestors

    def get_full_path(self):
        """e.g. Administration > Public Relations > Public Education"""
        parts = [a.name for a in self.get_ancestors()] + [self.name]
        return " > ".join(parts)

    @property
    def directorate(self):
        """Find the root Directorate ancestor or self if this is a Directorate."""
        if self.unit_type == self.FunctionalUnitType.DIRECTORATE:
            return self
        for ancestor in self.get_ancestors():
            if ancestor.unit_type == self.FunctionalUnitType.DIRECTORATE:
                return ancestor
        return None

    @property
    def department_or_division(self):
        """Find the Department or Division ancestor or self."""
        if self.unit_type in (self.FunctionalUnitType.DEPARTMENT, self.FunctionalUnitType.DIVISION):
            return self
        for ancestor in self.get_ancestors():
            if ancestor.unit_type in (self.FunctionalUnitType.DEPARTMENT, self.FunctionalUnitType.DIVISION):
                return ancestor
        return None

    def get_descendant_ids(self, include_self=True):
        """Return a set of IDs containing self and all recursive descendants."""
        ids = {self.id} if include_self else set()
        queue = [self.id]
        while queue:
            parent_id = queue.pop(0)
            children = FunctionalUnit.objects.filter(parent_id=parent_id).values_list('id', flat=True)
            for cid in children:
                if cid not in ids:
                    ids.add(cid)
                    queue.append(cid)
        return ids


class FunctionalOffice(models.Model):
    """
    Functional Organisational Structure across National, Regional, and District levels.
    Controls functional workspaces (Administration, Accounts, Operations, HR, Watch Room).
    Supports fallback responsibility (e.g. District Administration acts as fallback for unestablished HR).
    """
    class FunctionType(models.TextChoices):
        ADMINISTRATION = "ADMINISTRATION", "Administration"
        ACCOUNTS = "ACCOUNTS", "Accounts"
        OPERATIONS = "OPERATIONS", "Operations"
        HR = "HR", "Human Resource (HR)"
        WATCH_ROOM = "WATCH_ROOM", "Watch Room"

    class CommandLevel(models.TextChoices):
        NATIONAL = "NATIONAL", "National"
        REGIONAL = "REGIONAL", "Regional"
        DISTRICT = "DISTRICT", "District"

    name = models.CharField(max_length=150)
    function_type = models.CharField(max_length=30, choices=FunctionType.choices, db_index=True)
    command_level = models.CharField(max_length=20, choices=CommandLevel.choices, default=CommandLevel.DISTRICT, db_index=True)
    region = models.ForeignKey(
        Region,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='functional_offices'
    )
    district = models.ForeignKey(
        District,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='functional_offices'
    )
    parent = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='child_offices'
    )
    is_established = models.BooleanField(
        default=True,
        help_text="False if office is not physically/organisationally established at this command"
    )
    fallback_office = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='falling_back_offices',
        help_text="Office performing delegated responsibilities when this office is not established"
    )
    active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['command_level', 'function_type', 'name']
        verbose_name = "Functional Office"
        verbose_name_plural = "Functional Offices"

    def __str__(self):
        loc = self.district.name if self.district else (self.region.name if self.region else "National")
        est = "" if self.is_established else " (Not Established)"
        return f"{self.name} [{loc}]{est}"

    def get_effective_office(self):
        """Returns self if established, otherwise resolves through fallback_office chain."""
        curr = self
        visited = set()
        while curr and not curr.is_established and curr.fallback_office and curr.id not in visited:
            visited.add(curr.id)
            curr = curr.fallback_office
        return curr


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
    # Organisational Hierarchy Linkage (Command / Posting)
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

    # Functional Organisational Assignment
    functional_unit = models.ForeignKey(
        FunctionalUnit,
        on_delete=models.PROTECT,
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
        NOT_SET = "NOT_SET", "Not Set / Requires Review"

    gender = models.CharField(
        max_length=10,
        choices=Gender.choices,
        default=Gender.MALE,
        db_index=True
    )

    profile_picture = models.ImageField(
        upload_to="profile_pictures/",
        null=True,
        blank=True,
        help_text="Officer avatar / profile photograph (1:1 aspect ratio)"
    )

    @property
    def sex(self):
        """Synonym for gender to support official GNFS leave eligibility specification."""
        return self.gender

    @property
    def profile_picture_url(self):
        """Safely return profile picture URL or None if not set or missing."""
        if self.profile_picture:
            try:
                return self.profile_picture.url
            except ValueError:
                return None
        return None

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
    def current_region_id(self):
        reg = self.current_region
        return reg.id if reg else None

    @property
    def current_district(self):
        if self.station and self.station.district:
            return self.station.district
        return None

    @property
    def current_district_id(self):
        dist = self.current_district
        return dist.id if dist else None

    @property
    def current_directorate(self):
        primary = self.primary_functional_assignment
        if primary and primary.functional_unit:
            return primary.functional_unit.directorate
        if self.functional_unit:
            return self.functional_unit.directorate
        return None

    @property
    def current_department_or_division(self):
        primary = self.primary_functional_assignment
        if primary and primary.functional_unit:
            return primary.functional_unit.department_or_division
        if self.functional_unit:
            return self.functional_unit.department_or_division
        return None

    @property
    def current_functional_unit(self):
        primary = self.primary_functional_assignment
        if primary and primary.functional_unit:
            return primary.functional_unit
        return self.functional_unit

    @property
    def current_posting(self):
        return self.postings.filter(is_current=True).first()

    @property
    def current_command_appointment(self):
        """Returns active command appointment (RFO, DFO, MFO, 2IC) or None."""
        return self.command_appointments.filter(is_active=True).first()

    @property
    def primary_functional_assignment(self):
        """Returns primary active functional assignment or None."""
        return self.functional_assignments.filter(is_active=True, is_primary=True).first()

    @property
    def active_functional_assignments(self):
        """Returns all active functional assignments ordered with primary first."""
        return self.functional_assignments.filter(is_active=True).order_by('-is_primary', 'functional_unit__name')

    @property
    def user_account(self):
        """Return the User account linked to this personnel record."""
        asgn = getattr(self, 'user_assignment', None)
        if asgn and asgn.user:
            return asgn.user
        from staff.models import UserAssignment
        asgn = UserAssignment.objects.filter(personnel=self).first()
        if asgn and asgn.user:
            return asgn.user
        from django.contrib.auth import get_user_model
        User = get_user_model()
        return User.objects.filter(username__iexact=self.staffID).first()

    @property
    def user_assignments(self):
        """Backward-compatible queryset for user assignments."""
        from staff.models import UserAssignment
        return UserAssignment.objects.filter(personnel=self)

    @property
    def active_administrative_assignments(self):
        """Returns all currently active administrative assignments."""
        return self.administrative_assignments.filter(is_active=True).select_related('role', 'region', 'district')

    @property
    def current_admin_role(self):
        """Returns highest active administrative role code or None."""
        asgn = self.active_administrative_assignments.first()
        return asgn.role.code if asgn else None


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
    reason = models.TextField(blank=True, default='')
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


class LeavePolicy(models.Model):
    class CalculationMode(models.TextChoices):
        WORKING_DAYS = "WORKING_DAYS", "Working Days (Excludes Weekends & Public Holidays)"
        CALENDAR_DAYS = "CALENDAR_DAYS", "Calendar Days (Includes Weekends & Holidays)"
        DATE_RANGE = "DATE_RANGE", "Date Range (Medical / Study Direction)"
        CALENDAR_MONTHS = "CALENDAR_MONTHS", "Calendar Months"

    class EligibleSex(models.TextChoices):
        ALL = "ALL", "All Personnel"
        MALE = "MALE", "Male Only"
        FEMALE = "FEMALE", "Female Only"

    leave_type = models.ForeignKey(
        LeaveType,
        on_delete=models.CASCADE,
        related_name="policies",
        db_index=True
    )
    name = models.CharField(max_length=150)
    policy_code = models.CharField(max_length=50, unique=True, db_index=True)
    eligible_sex = models.CharField(
        max_length=10,
        choices=EligibleSex.choices,
        default=EligibleSex.ALL,
        db_index=True
    )
    officer_category = models.CharField(
        max_length=10,
        choices=Rank.OfficerCategory.choices,
        blank=True,
        default="",
        db_index=True,
        help_text="Senior / Junior or blank for all"
    )
    duration = models.PositiveIntegerField(
        default=0,
        help_text="Base entitlement or fixed duration in days (or months if CALENDAR_MONTHS)"
    )
    calculation_mode = models.CharField(
        max_length=20,
        choices=CalculationMode.choices,
        default=CalculationMode.WORKING_DAYS
    )
    requires_document = models.BooleanField(default=False)
    is_core_policy = models.BooleanField(
        default=False,
        help_text="Protected national core policy that cannot be altered or deleted by regional/district admins"
    )
    is_active = models.BooleanField(default=True, db_index=True)
    description = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['leave_type__name', 'officer_category', 'eligible_sex']
        verbose_name = "Leave Policy"
        verbose_name_plural = "Leave Policies"

    def __str__(self):
        return f"{self.name} ({self.duration} {self.get_calculation_mode_display()})"


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
    is_prorated = models.BooleanField(default=False, help_text="Indicates proportionate / prorated annual entitlement")
    proration_reason = models.TextField(blank=True, default="", help_text="Recorded operational/service reason for proration")
    full_annual_entitlement = models.PositiveIntegerField(default=0, help_text="Standard full-year entitlement before proration")
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
    def total_entitlement(self):
        """Synonym for total_available."""
        return self.total_available

    @property
    def carry_forward_days(self):
        """Synonym for carried_forward."""
        return self.carried_forward

    @property
    def remaining_balance(self):
        return max(0, self.total_available - self.days_taken - self.days_reserved)

    def __str__(self):
        return f"{self.personnel.fullname} - {self.leave_type.name} ({self.leave_year}): {self.remaining_balance} left"


class LeaveRequest(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SUBMITTED = "SUBMITTED", "Submitted"
        PENDING_ADMIN_REVIEW = "PENDING_ADMIN_REVIEW", "Awaiting Administration Review"
        RETURNED_FOR_CORRECTION = "RETURNED_FOR_CORRECTION", "Returned for Correction"
        PENDING_OPERATIONS_RECOMMENDATION = "PENDING_OPERATIONS_RECOMMENDATION", "Awaiting Operations Recommendation"
        OPERATIONS_RECOMMENDED = "OPERATIONS_RECOMMENDED", "Operations Recommended"
        OPERATIONS_NOT_RECOMMENDED = "OPERATIONS_NOT_RECOMMENDED", "Operations Not Recommended"
        PENDING_DFO_MFO_DECISION = "PENDING_DFO_MFO_DECISION", "Awaiting DFO/MFO Command Decision"
        PENDING_APPROVAL = "PENDING_APPROVAL", "Awaiting Command Decision"
        APPROVED = "APPROVED", "Approved"
        APPROVED_PENDING_ADMIN_PROCESSING = "APPROVED_PENDING_ADMIN_PROCESSING", "Approved - Awaiting Final Administration Processing"
        REJECTED = "REJECTED", "Rejected"
        PENDING_ADMIN_FINAL_PROCESSING = "PENDING_ADMIN_FINAL_PROCESSING", "Approved - Awaiting Final Administration Processing"
        SENT_TO_WATCH_ROOM = "SENT_TO_WATCH_ROOM", "Sent to Watch Room"
        BOOKED = "BOOKED", "Booked in Watch Room"
        ACTIVE = "ACTIVE", "Active / On Leave"
        DUE_TO_RESUME = "DUE_TO_RESUME", "Due to Resume Duty"
        RESUMED = "RESUMED", "Resumed Duty"
        COMPLETED = "COMPLETED", "Completed / Returned to Post"
        CANCELLED = "CANCELLED", "Cancelled"
        RECALLED = "RECALLED", "Recalled / Called Off"
        # Backward compatibility synonym:
        PENDING_PROCESSING = "PENDING_PROCESSING", "Awaiting Processing"

    class ProcessingStage(models.TextChoices):
        DISTRICT_ADMIN = "DISTRICT_ADMIN", "District Administration"
        DISTRICT_OPERATIONS = "DISTRICT_OPERATIONS", "District Operations"
        REGIONAL_ADMIN = "REGIONAL_ADMIN", "Regional Administration"
        REGIONAL_OPERATIONS = "REGIONAL_OPERATIONS", "Regional Operations"
        COMMAND_APPROVAL = "COMMAND_APPROVAL", "Command Final Approval"
        WATCH_ROOM = "WATCH_ROOM", "Watch Room Booking"
        HIGHER_AUTHORITY = "HIGHER_AUTHORITY", "Higher Approval Authority"
        COMPLETED = "COMPLETED", "Workflow Completed"

    reference_number = models.CharField(max_length=50, unique=True, null=True, blank=True, db_index=True)
    creation_mode = models.CharField(
        max_length=30,
        choices=[('PERSONNEL', 'Personnel Initiated'), ('ADMIN', 'Administration Created')],
        default='PERSONNEL',
        db_index=True
    )

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
        max_length=40,
        choices=Status.choices,
        default=Status.PENDING_ADMIN_REVIEW,
        db_index=True
    )

    # Workflow Routing & Multi-Tier Stages
    processing_stage = models.CharField(
        max_length=30,
        choices=ProcessingStage.choices,
        default=ProcessingStage.DISTRICT_ADMIN,
        db_index=True
    )
    routing_district = models.ForeignKey(
        'District',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="routed_leave_requests"
    )
    routing_region = models.ForeignKey(
        'Region',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="routed_leave_requests"
    )
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="leave_requests_processed"
    )
    processed_at = models.DateTimeField(null=True, blank=True)
    processing_notes = models.TextField(blank=True, default="")
    correction_reason = models.TextField(blank=True, default="")

    # Operations Functional Recommendation (Section 48-49)
    operations_recommendation = models.CharField(max_length=30, blank=True, default="")  # RECOMMEND, NOT_RECOMMEND
    operations_recommended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="leave_recommendations_made"
    )
    operations_recommended_at = models.DateTimeField(null=True, blank=True)
    operations_comments = models.TextField(blank=True, default="")

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
    approval_comment = models.TextField(blank=True, default="")
    approval_appointment = models.ForeignKey(
        'CommandAppointment',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="command_approved_leaves"
    )

    # Watch Room Operational Booking (Section 40-43, 54)
    sent_to_watch_room_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="leaves_sent_to_watch_room"
    )
    sent_to_watch_room_at = models.DateTimeField(null=True, blank=True)
    watch_room_booked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="leaves_booked_in_watch_room"
    )
    watch_room_booked_at = models.DateTimeField(null=True, blank=True)
    watch_room_book_type = models.CharField(max_length=50, blank=True, default="STATION_DIARY")
    watch_room_book_reference = models.CharField(max_length=100, blank=True, default="")
    watch_room_remarks = models.TextField(blank=True, default="")
    resumed_at = models.DateTimeField(null=True, blank=True)
    resumed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="leaves_resumed_recorded"
    )
    resumption_remarks = models.TextField(blank=True, default="")

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

        if not self.reference_number:
            year = self.leave_year or (self.start_date.year if self.start_date else timezone.now().year)
            prefix = f"LEV-{year}-"
            last_req = LeaveRequest.objects.filter(reference_number__startswith=prefix).order_by('-id').first()
            seq = 1
            if last_req and last_req.reference_number:
                try:
                    parts = last_req.reference_number.split('-')
                    seq = int(parts[-1]) + 1
                except (ValueError, IndexError):
                    seq = (self.id or 1)
            elif self.id:
                seq = self.id
            self.reference_number = f"LEV-{year}-{seq:06d}"

        super().save(*args, **kwargs)

    class Meta:
        ordering = ['-start_date', '-created_at']
        verbose_name = "Leave Request"
        verbose_name_plural = "Leave Requests"

    def __str__(self):
        ref = f"[{self.reference_number}] " if self.reference_number else ""
        return f"{ref}{self.personnel.fullname} - {self.leave_type.name} ({self.start_date} to {self.last_leave_date}) [{self.get_status_display()}]"


# Backward compatibility aliases
LeaveRequest.Status.RECOMMENDED = LeaveRequest.Status.PENDING_APPROVAL
LeaveRequest.ProcessingStage.RFO = LeaveRequest.ProcessingStage.COMMAND_APPROVAL
LeaveRequest.ProcessingStage.DFO = LeaveRequest.ProcessingStage.COMMAND_APPROVAL
LeaveRequest.ProcessingStage.MFO = LeaveRequest.ProcessingStage.COMMAND_APPROVAL


class LeaveApprovalDecision(models.Model):
    DECISION_CHOICES = [
        ('SUBMITTED', 'Submitted'),
        ('FORWARDED', 'Reviewed & Forwarded'),
        ('FORWARDED_TO_OPERATIONS', 'Reviewed & Forwarded to Operations'),
        ('OPERATIONS_RECOMMENDED', 'Operations Recommended'),
        ('OPERATIONS_NOT_RECOMMENDED', 'Operations Not Recommended'),
        ('RETURNED_FOR_CORRECTION', 'Returned for Correction'),
        ('RESUBMITTED', 'Resubmitted'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
        ('SENT_TO_WATCH_ROOM', 'Sent to Watch Room'),
        ('BOOKED', 'Booked in Watch Room'),
        ('RESUMED', 'Resumed Duty'),
        ('RECALLED', 'Recalled'),
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
    approver_role = models.CharField(max_length=100)
    decision = models.CharField(max_length=35, choices=DECISION_CHOICES)
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


class WatchRoomBooking(models.Model):
    """
    District Watch Room official operational recording of approved leave (Section 40-43, 54).
    Receives approved leave record from District Administration for operational log book entry.
    """
    leave_request = models.OneToOneField(
        LeaveRequest,
        on_delete=models.CASCADE,
        related_name="watch_room_booking_record"
    )
    district = models.ForeignKey(
        District,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="watch_room_bookings"
    )
    booked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="watch_room_bookings_created"
    )
    booked_at = models.DateTimeField(default=timezone.now)
    book_type = models.CharField(max_length=50, default="STATION_DIARY")
    book_reference = models.CharField(max_length=100, blank=True, default="", help_text="Station diary volume / page number")
    remarks = models.TextField(blank=True, default="")
    resumed_at = models.DateTimeField(null=True, blank=True)
    resumed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="watch_room_resumptions_recorded"
    )
    resumption_remarks = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-booked_at']
        verbose_name = "Watch Room Booking"
        verbose_name_plural = "Watch Room Bookings"

    def __str__(self):
        ref = self.leave_request.reference_number or str(self.leave_request.id)
        return f"Watch Room Booking #{self.id} for {ref} ({self.book_reference or 'No ref'})"


class LeaveWorkflowHistory(models.Model):
    """
    Complete audit timeline for every workflow action and state change on a Leave Request (Section 56-57).
    """
    leave_request = models.ForeignKey(
        LeaveRequest,
        on_delete=models.CASCADE,
        related_name="workflow_history"
    )
    action = models.CharField(max_length=60)
    from_status = models.CharField(max_length=40, blank=True, default='')
    to_status = models.CharField(max_length=40)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    actor_role = models.CharField(max_length=100, blank=True, default='')
    functional_office = models.CharField(max_length=100, blank=True, default='')
    comment = models.TextField(blank=True, default='')
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['timestamp']
        verbose_name = "Leave Workflow History"
        verbose_name_plural = "Leave Workflow Histories"

    def __str__(self):
        return f"{self.action} on Leave #{self.leave_request_id} by {getattr(self.actor, 'username', 'System')} at {self.timestamp}"


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


# ==============================================================================
# FUNCTIONAL ROLE ASSIGNMENT MODEL
# ==============================================================================

class FunctionalRoleType(models.TextChoices):
    DIRECTORATE_HEAD = "DIRECTORATE_HEAD", "Directorate Head"
    REGIONAL_FUNCTION_HEAD = "REGIONAL_FUNCTION_HEAD", "Regional Function Head"
    DISTRICT_FUNCTION_HEAD = "DISTRICT_FUNCTION_HEAD", "District Function Head"
    FUNCTION_ADMIN = "FUNCTION_ADMIN", "Function Administrator"
    FUNCTION_MEMBER = "FUNCTION_MEMBER", "Function Member"
    DEPARTMENT_HEAD = "DEPARTMENT_HEAD", "Department / Division Head"
    UNIT_HEAD = "UNIT_HEAD", "Unit Head"
    SECTION_HEAD = "SECTION_HEAD", "Section Head"
    UNIT_ADMIN = "UNIT_ADMIN", "Unit Administrator"
    MEMBER = "MEMBER", "Member"


class FunctionalRoleAssignment(models.Model):
    """
    Explicit functional management assignments (Directorate Head, Regional Function Head, District Function Head).
    Separates functional membership from functional responsibility/leadership (Section 9-10).
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="functional_roles"
    )
    personnel = models.ForeignKey(
        NorminalRoll,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="functional_management_roles",
        db_index=True
    )
    organisational_unit = models.ForeignKey(
        FunctionalUnit,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="role_assignments"
    )
    functional_office = models.ForeignKey(
        'FunctionalOffice',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="management_assignments"
    )
    role = models.CharField(
        max_length=35,
        choices=FunctionalRoleType.choices,
        default=FunctionalRoleType.MEMBER,
        db_index=True
    )
    scope_level = models.CharField(
        max_length=20,
        choices=FunctionalOffice.CommandLevel.choices,
        default=FunctionalOffice.CommandLevel.DISTRICT,
        db_index=True
    )
    region = models.ForeignKey(
        Region,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="functional_role_assignments"
    )
    district = models.ForeignKey(
        District,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="functional_role_assignments"
    )
    active = models.BooleanField(default=True, db_index=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_functional_roles"
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    effective_from = models.DateField(default=timezone.now)
    effective_to = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['role', '-assigned_at']
        verbose_name = "Functional Role Assignment"
        verbose_name_plural = "Functional Role Assignments"

    def save(self, *args, **kwargs):
        if self.personnel and not self.user_id:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            matched_user = User.objects.filter(username=self.personnel.staffID).first()
            if matched_user:
                self.user = matched_user
        if self.functional_office:
            if not self.scope_level:
                self.scope_level = self.functional_office.command_level
            if not self.region and self.functional_office.region:
                self.region = self.functional_office.region
            if not self.district and self.functional_office.district:
                self.district = self.functional_office.district
        super().save(*args, **kwargs)

    def __str__(self):
        who = self.personnel.fullname if self.personnel else (self.user.username if self.user else "Unknown")
        target = self.functional_office.name if self.functional_office else (self.organisational_unit.name if self.organisational_unit else "General")
        return f"{who} - {self.get_role_display()} ({target})"


# ==============================================================================
# PERSONNEL POSTINGS & TRANSFER WORKFLOW MODELS
# ==============================================================================

class PersonnelPosting(models.Model):
    personnel = models.ForeignKey(
        NorminalRoll,
        on_delete=models.CASCADE,
        related_name="postings",
        db_index=True
    )
    region = models.ForeignKey(
        Region,
        on_delete=models.PROTECT,
        related_name="postings"
    )
    district = models.ForeignKey(
        District,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="postings"
    )
    station = models.ForeignKey(
        Station,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="postings"
    )
    post = models.ForeignKey(
        Post,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="postings"
    )
    start_date = models.DateField(default=timezone.now)
    end_date = models.DateField(null=True, blank=True)
    is_current = models.BooleanField(default=True, db_index=True)
    source_transfer = models.ForeignKey(
        'PersonnelTransfer',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resulting_postings"
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="command_postings_assigned"
    )
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="command_postings_confirmed"
    )
    remarks = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-start_date', '-created_at']
        verbose_name = "Personnel Posting Record"
        verbose_name_plural = "Personnel Posting Records"

    def __str__(self):
        loc = self.station.name if self.station else self.region.name
        status = "Current" if self.is_current else f"Ended {self.end_date}"
        return f"{self.personnel.fullname} @ {loc} ({status})"


class PersonnelFunctionalAssignment(models.Model):
    personnel = models.ForeignKey(
        NorminalRoll,
        on_delete=models.CASCADE,
        related_name="functional_assignments",
        db_index=True
    )
    functional_unit = models.ForeignKey(
        FunctionalUnit,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="personnel_assignments"
    )
    functional_office = models.ForeignKey(
        'FunctionalOffice',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="personnel_functional_assignments"
    )
    assignment_role = models.CharField(max_length=100, blank=True, default='Member')
    is_primary = models.BooleanField(default=False, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    start_date = models.DateField(default=timezone.now)
    end_date = models.DateField(null=True, blank=True)
    is_current = models.BooleanField(default=True, db_index=True)
    reason = models.TextField(blank=True, default='')
    source_transfer = models.ForeignKey(
        'PersonnelTransfer',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resulting_functional_assignments"
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="functional_assignments_assigned"
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="functional_assignments_approved"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_primary', '-start_date', '-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['personnel', 'functional_unit'],
                condition=models.Q(is_active=True, functional_unit__isnull=False),
                name='unique_active_assignment_per_personnel_and_unit'
            ),
            models.UniqueConstraint(
                fields=['personnel'],
                condition=models.Q(is_active=True, is_primary=True),
                name='unique_active_primary_assignment_per_personnel'
            ),
        ]
        verbose_name = "Personnel Functional Assignment Record"
        verbose_name_plural = "Personnel Functional Assignment Records"

    @property
    def organisational_unit(self):
        return self.functional_unit or self.functional_office

    @property
    def effective_from(self):
        return self.start_date

    @property
    def effective_to(self):
        return self.end_date

    def save(self, *args, **kwargs):
        # Synchronize is_current and is_active
        if self.is_active != self.is_current:
            if 'is_active' in kwargs.get('update_fields', []):
                self.is_current = self.is_active
            elif 'is_current' in kwargs.get('update_fields', []):
                self.is_active = self.is_current
            else:
                self.is_current = self.is_active
        super().save(*args, **kwargs)

    def __str__(self):
        pri = " [PRIMARY]" if self.is_primary else ""
        status = "Active" if self.is_active else f"Ended {self.end_date}"
        return f"{self.personnel.fullname} @ {self.functional_unit.name} - {self.assignment_role}{pri} ({status})"


class CommandAppointment(models.Model):
    class AppointmentType(models.TextChoices):
        RFO = "RFO", "Regional Fire Officer (RFO)"
        REGIONAL_2IC = "REGIONAL_2IC", "Regional Second-in-Command (2IC)"
        DFO = "DFO", "District Fire Officer (DFO)"
        MFO = "MFO", "Municipal Fire Officer (MFO)"
        METRO_FO = "METRO_FO", "Metropolitan Fire Officer (MFO)"
        DISTRICT_2IC = "DISTRICT_2IC", "District Second-in-Command (2IC)"
        STATION_OFFICER = "STATION_OFFICER", "Station Officer-in-Charge"
        ACTING_RFO = "ACTING_RFO", "Acting Regional Fire Officer"
        ACTING_REGIONAL_2IC = "ACTING_REGIONAL_2IC", "Acting Regional 2IC"
        ACTING_DFO = "ACTING_DFO", "Acting District Fire Officer"
        ACTING_MFO = "ACTING_MFO", "Acting Municipal Fire Officer"
        ACTING_METRO_FO = "ACTING_METRO_FO", "Acting Metropolitan Fire Officer"
        ACTING_DISTRICT_2IC = "ACTING_DISTRICT_2IC", "Acting District 2IC"

    personnel = models.ForeignKey(
        NorminalRoll,
        on_delete=models.CASCADE,
        related_name="command_appointments",
        db_index=True
    )
    appointment_type = models.CharField(
        max_length=40,
        choices=AppointmentType.choices,
        db_index=True
    )
    region = models.ForeignKey(
        Region,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="command_appointments"
    )
    district = models.ForeignKey(
        District,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="command_appointments"
    )
    station = models.ForeignKey(
        Station,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="command_appointments"
    )
    post = models.ForeignKey(
        Post,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="command_appointments"
    )
    effective_from = models.DateField(default=timezone.now)
    effective_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    is_acting = models.BooleanField(default=False)
    appointed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="command_appointments_made"
    )
    appointment_reference = models.CharField(max_length=100, blank=True, default='')
    remarks = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_active', '-effective_from', '-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['region'],
                condition=models.Q(is_active=True, appointment_type__in=['RFO', 'ACTING_RFO']),
                name='unique_active_rfo_per_region'
            ),
            models.UniqueConstraint(
                fields=['region'],
                condition=models.Q(is_active=True, appointment_type__in=['REGIONAL_2IC', 'ACTING_REGIONAL_2IC']),
                name='unique_active_regional_2ic_per_region'
            ),
            models.UniqueConstraint(
                fields=['district'],
                condition=models.Q(
                    is_active=True,
                    appointment_type__in=['DFO', 'MFO', 'METRO_FO', 'ACTING_DFO', 'ACTING_MFO', 'ACTING_METRO_FO']
                ),
                name='unique_active_commanding_officer_per_district'
            ),
            models.UniqueConstraint(
                fields=['district'],
                condition=models.Q(is_active=True, appointment_type__in=['DISTRICT_2IC', 'ACTING_DISTRICT_2IC']),
                name='unique_active_district_2ic_per_district'
            ),
        ]
        verbose_name = "Command Appointment"
        verbose_name_plural = "Command Appointments"

    def __str__(self):
        target = self.district.name if self.district else (self.region.name if self.region else (self.station.name if self.station else "HQ"))
        status = "Active" if self.is_active else f"Ended {self.effective_to}"
        acting = " (Acting)" if self.is_acting else ""
        return f"{self.personnel.fullname} - {self.get_appointment_type_display()}{acting} @ {target} ({status})"

    def end_appointment(self, user=None, end_date=None, remarks=""):
        self.is_active = False
        self.effective_to = end_date or timezone.now().date()
        if remarks:
            audit_note = f"[Ended by {user or 'System'} on {self.effective_to}]: {remarks}"
            self.remarks = f"{self.remarks}\n{audit_note}".strip()
        self.save(update_fields=['is_active', 'effective_to', 'remarks'])


class TransferType(models.TextChoices):
    INTER_REGIONAL = "INTER_REGIONAL", "Inter-Regional Transfer"
    INTER_DISTRICT = "INTER_DISTRICT", "Inter-District Transfer"
    INTER_STATION = "INTER_STATION", "Inter-Station Transfer"
    INTER_POST = "INTER_POST", "Inter-Post Transfer"
    FUNCTIONAL_TRANSFER = "FUNCTIONAL_TRANSFER", "Functional Reassignment"
    COMBINED_TRANSFER = "COMBINED_TRANSFER", "Command Posting & Functional Reassignment"


class TransferStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    PENDING_APPROVAL = "PENDING_APPROVAL", "Pending Approval"
    APPROVED = "APPROVED", "Approved"
    RELEASED = "RELEASED", "Released (Awaiting Reporting)"
    REPORTED = "REPORTED", "Reported (Awaiting Confirmation)"
    CONFIRMED = "CONFIRMED", "Confirmed (Posting Active)"
    REJECTED = "REJECTED", "Rejected"
    CANCELLED = "CANCELLED", "Cancelled"


# Backward compatibility alias
TransferStatus.AWAITING_REPORTING = TransferStatus.RELEASED
TransferStatus.ARRIVED = TransferStatus.CONFIRMED


class PersonnelTransfer(models.Model):
    reference_number = models.CharField(max_length=50, unique=True, db_index=True)
    personnel = models.ForeignKey(
        NorminalRoll,
        on_delete=models.CASCADE,
        related_name="transfers",
        db_index=True
    )
    transfer_type = models.CharField(
        max_length=30,
        choices=TransferType.choices,
        db_index=True
    )

    # Source Command Hierarchy
    source_region = models.ForeignKey(
        Region,
        on_delete=models.PROTECT,
        related_name="outgoing_transfers"
    )
    source_district = models.ForeignKey(
        District,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="outgoing_transfers"
    )
    source_station = models.ForeignKey(
        Station,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="outgoing_transfers"
    )
    source_post = models.ForeignKey(
        Post,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="outgoing_transfers"
    )

    # Source Functional Hierarchy
    source_functional_unit = models.ForeignKey(
        FunctionalUnit,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="outgoing_functional_transfers"
    )

    # Destination Command Hierarchy
    destination_region = models.ForeignKey(
        Region,
        on_delete=models.PROTECT,
        related_name="incoming_transfers"
    )
    destination_district = models.ForeignKey(
        District,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="incoming_transfers"
    )
    destination_station = models.ForeignKey(
        Station,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="incoming_transfers"
    )
    destination_post = models.ForeignKey(
        Post,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="incoming_transfers"
    )

    # Destination Functional Hierarchy
    destination_functional_unit = models.ForeignKey(
        FunctionalUnit,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="incoming_functional_transfers"
    )

    transfer_letter_number = models.CharField(max_length=100)
    transfer_letter_date = models.DateField(null=True, blank=True)
    effective_date = models.DateField()
    expected_reporting_date = models.DateField()
    actual_reporting_date = models.DateField(null=True, blank=True)
    attachment = models.FileField(upload_to="transfer_letters/%Y/%m/", null=True, blank=True)
    reason = models.TextField()
    remarks = models.TextField(blank=True, default='')

    status = models.CharField(
        max_length=30,
        choices=TransferStatus.choices,
        default=TransferStatus.DRAFT,
        db_index=True
    )

    # Workflow tracking
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="transfers_initiated"
    )
    initiated_at = models.DateTimeField(auto_now_add=True)

    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transfers_released"
    )
    released_at = models.DateTimeField(null=True, blank=True)

    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transfers_reported"
    )
    reported_at = models.DateTimeField(null=True, blank=True)

    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transfers_confirmed"
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)

    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transfers_cancelled"
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Personnel Transfer"
        verbose_name_plural = "Personnel Transfers"

    def __str__(self):
        return f"{self.reference_number}: {self.personnel.fullname} ({self.get_transfer_type_display()} - {self.get_status_display()})"

    @property
    def from_station(self):
        return self.source_station

    @property
    def to_station(self):
        return self.destination_station

    @property
    def from_region(self):
        return self.source_region

    @property
    def to_region(self):
        return self.destination_region

    @property
    def is_overdue(self):
        if self.status in (TransferStatus.RELEASED, TransferStatus.REPORTED):
            return self.expected_reporting_date < timezone.now().date()
        return False


class TransferApprovalHistory(models.Model):
    transfer = models.ForeignKey(
        PersonnelTransfer,
        on_delete=models.CASCADE,
        related_name="history"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="transfer_history_actions"
    )
    from_status = models.CharField(max_length=30)
    to_status = models.CharField(max_length=30)
    action = models.CharField(max_length=50)
    comments = models.TextField(blank=True, default='')
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['timestamp']
        verbose_name = "Transfer Approval History"
        verbose_name_plural = "Transfer Approval Histories"

    def __str__(self):
        return f"[{self.timestamp.strftime('%Y-%m-%d %H:%M')}] {self.action} on {self.transfer.reference_number} by {self.actor.username}"


# ==============================================================================
# PROFESSIONAL ROLE-BASED ACCESS CONTROL & ADMINISTRATIVE ASSIGNMENT MODELS
# ==============================================================================

class ApplicationRole(models.Model):
    """
    Configurable Application Role representing administrative authorization.
    Standard system roles: SUPER_ADMIN, REGIONAL_ADMIN, DISTRICT_ADMIN.
    """
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=50, unique=True, db_index=True)
    description = models.TextField(blank=True, default='')
    is_active = models.BooleanField(default=True, db_index=True)
    is_system_role = models.BooleanField(default=False, help_text="System-defined roles cannot be deleted.")
    permissions = models.ManyToManyField(
        'AppPermission',
        through='RolePermission',
        related_name='roles',
        blank=True
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="roles_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = "Application Role"
        verbose_name_plural = "Application Roles"

    def __str__(self):
        return f"{self.name} ({self.code})"


class AppPermission(models.Model):
    """
    Granular permission item belonging to an operational module.
    """
    class ModuleChoices(models.TextChoices):
        PERSONNEL = "PERSONNEL", "Personnel Management"
        COMMAND_STRUCTURE = "COMMAND_STRUCTURE", "Command Structure"
        ADMINISTRATION = "ADMINISTRATION", "Administration"
        ROLES_PERMISSIONS = "ROLES_PERMISSIONS", "Roles & Permissions"
        COMMAND_APPOINTMENTS = "COMMAND_APPOINTMENTS", "Command Appointments"
        LEAVE = "LEAVE", "Leave Administration"
        TRANSFERS = "TRANSFERS", "Transfers"
        ORGANISATION = "ORGANISATION", "Organisation"
        REPORTS_AUDIT = "REPORTS_AUDIT", "Reports & Audit"
        ACCOUNTS = "ACCOUNTS", "Accounts & Nominal Roll"
        OPERATIONS = "OPERATIONS", "Operations"
        WATCH_ROOM = "WATCH_ROOM", "Watch Room"
        HR = "HR", "Human Resources"

    code = models.CharField(max_length=80, unique=True, db_index=True)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True, default='')
    module = models.CharField(max_length=50, choices=ModuleChoices.choices, db_index=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['module', 'code']
        verbose_name = "Permission"
        verbose_name_plural = "Permissions"

    def __str__(self):
        return f"[{self.module}] {self.name} ({self.code})"

    def get_module_display(self):
        """
        Return human-readable display name for the permission module.
        """
        for code, label in self.ModuleChoices.choices:
            if self.module == code:
                return label
        return (self.module or "").replace('_', ' ').title() if self.module else "General"


class RolePermission(models.Model):
    """
    Link between an ApplicationRole and an AppPermission.
    """
    role = models.ForeignKey(
        ApplicationRole,
        on_delete=models.CASCADE,
        related_name="role_permissions"
    )
    permission = models.ForeignKey(
        AppPermission,
        on_delete=models.CASCADE,
        related_name="role_permissions"
    )

    class Meta:
        unique_together = ('role', 'permission')
        verbose_name = "Role Permission"
        verbose_name_plural = "Role Permissions"

    def __str__(self):
        return f"{self.role.code} -> {self.permission.code}"


class AdministrativeAssignment(models.Model):
    """
    Audited historical assignment of an administrative role or functional responsibility to an officer.
    Scoped to National (region=None, district=None), Regional (region set), or District.
    """
    class AssignmentType(models.TextChoices):
        ADMINISTRATIVE = "ADMINISTRATIVE", "Application Administration"
        FUNCTIONAL = "FUNCTIONAL", "Functional Responsibility"

    class ScopeLevel(models.TextChoices):
        NATIONAL = "NATIONAL", "National"
        REGIONAL = "REGIONAL", "Regional"
        DISTRICT = "DISTRICT", "District"

    personnel = models.ForeignKey(
        'NorminalRoll',
        on_delete=models.CASCADE,
        related_name="administrative_assignments"
    )
    assignment_type = models.CharField(
        max_length=30,
        choices=AssignmentType.choices,
        default=AssignmentType.ADMINISTRATIVE,
        db_index=True
    )
    level = models.CharField(
        max_length=20,
        choices=ScopeLevel.choices,
        default=ScopeLevel.DISTRICT,
        db_index=True
    )
    role = models.ForeignKey(
        ApplicationRole,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="assignments"
    )
    region = models.ForeignKey(
        Region,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="administrative_assignments"
    )
    district = models.ForeignKey(
        District,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="administrative_assignments"
    )
    station = models.ForeignKey(
        Station,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="administrative_assignments"
    )
    directorate = models.ForeignKey(
        'FunctionalUnit',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="administrative_directorate_assignments"
    )
    department = models.ForeignKey(
        'FunctionalUnit',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="administrative_department_assignments"
    )
    unit = models.ForeignKey(
        'FunctionalUnit',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="administrative_unit_assignments"
    )
    functional_office = models.ForeignKey(
        'FunctionalOffice',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="administrative_office_assignments"
    )
    responsibility_title = models.CharField(max_length=150, blank=True, default='')
    remarks = models.TextField(blank=True, default='')

    effective_from = models.DateField(default=timezone.localdate)
    effective_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="admin_roles_assigned"
    )
    assigned_at = models.DateTimeField(auto_now_add=True)

    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="admin_roles_revoked"
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    revocation_reason = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-effective_from', '-created_at']
        verbose_name = "Administrative Assignment"
        verbose_name_plural = "Administrative Assignments"

    @property
    def display_role_name(self):
        if self.role:
            return self.role.name
        return self.responsibility_title or "Role / Responsibility"

    def __str__(self):
        scope_str = self.level.title() if hasattr(self, 'level') and self.level else "National"
        if self.station:
            scope_str = f"{self.station.name} Station"
        elif self.district:
            scope_str = f"{self.district.name} District"
        elif self.region:
            scope_str = f"{self.region.name} Region"
        status = "ACTIVE" if self.is_active else "REVOKED/ENDED"
        return f"{self.personnel.fullname} - {self.display_role_name} [{scope_str}] ({status})"



