from django.db import models
from django.core.validators import RegexValidator
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
    ('THERMINAL LEAVE','THERMINAL LEAVE'),
    ('MATERNITY LEAVE', 'MATERNITY LEAVE'),
    ('STUDY LEAVE', 'STUDY LEAVE'),
    ('SICK LEAVE', 'SICK LEAVE'),
]

class NorminalRoll(models.Model):
    staffID = models.CharField(
        max_length=10,
        validators=[
            RegexValidator(r'^\d{5,10}$', 'Staff ID must be exactly 7 digits.')
        ],
        unique=True
    )
    fullname = models.CharField(max_length=255)
    job_grade = models.CharField(max_length=50)
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

    def save(self, *args, **kwargs):
      """Apply leave rules: called_off, expired leave, or manual override."""
      today = timezone.now().date()

      if self.called_off:
        # Rule 1: Called off → force reset
        self.leave_type = "AT POST"
        self.leave_status = "AT POST"
        self.start_leave = None
        self.end_leave = None

      elif self.manual_override:
        # Rule 2: Manual override → respect user selection
        if not self.leave_status:
            self.leave_status = "AT POST"

      elif self.start_leave and self.end_leave:
        # Rule 3: Manual dates provided → assume ON LEAVE
        self.leave_status = "ON LEAVE"

      else:
        # Rule 4: Auto logic (fallback)
        if self.start_leave and self.end_leave:
            if self.start_leave <= today <= self.end_leave:
                self.leave_status = "ON LEAVE"
            else:
                self.leave_status = "AT POST"
        else:
            if not self.leave_status:
                self.leave_status = "AT POST"

      super().save(*args, **kwargs)


    
    def __str__(self):
        return f"{self.staffID} - {self.fullname}"
