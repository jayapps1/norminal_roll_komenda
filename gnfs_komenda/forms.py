from django import forms
from django.db import models
from django.utils import timezone
from django.db.models import Q
from gnfs_komenda.models import (
    Region, District, Station, Post, NorminalRoll, Rank,
    FunctionalUnit, FunctionalRoleAssignment, FunctionalRoleType,
    PersonnelTransfer, TransferType, TransferStatus, PublicHoliday,
    CommandAppointment, PersonnelFunctionalAssignment
)
from gnfs_komenda.security import get_stations_queryset_for_user, get_posts_queryset_for_user


class NorminalRollForm(forms.ModelForm):
    officer_category = forms.ChoiceField(
        choices=[
            ('', 'Select Officer Category'),
            (Rank.OfficerCategory.SENIOR, 'Senior Officer'),
            (Rank.OfficerCategory.JUNIOR, 'Junior Officer'),
        ],
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_officer_category'}),
        required=True,
        label="Officer Category"
    )
    rank = forms.ModelChoiceField(
        queryset=Rank.objects.filter(active=True).order_by('hierarchy_order'),
        empty_label="Select Rank / Job Grade",
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_rank'}),
        required=True,
        label="Rank / Job Grade"
    )
    birth_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        input_formats=['%Y-%m-%d'],
        required=False
    )
    start_leave = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        input_formats=['%Y-%m-%d'],
        required=False
    )
    end_leave = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        input_formats=['%Y-%m-%d'],
        required=False
    )

    # Functional Organisational Structure Cascading Dropdowns
    directorate = forms.ModelChoiceField(
        queryset=FunctionalUnit.objects.filter(parent__isnull=True, active=True).order_by('display_order', 'name'),
        empty_label="Select Directorate",
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_directorate'}),
        required=False,
        label="Directorate"
    )
    department = forms.ModelChoiceField(
        queryset=FunctionalUnit.objects.filter(
            unit_type__in=[FunctionalUnit.FunctionalUnitType.DEPARTMENT, FunctionalUnit.FunctionalUnitType.DIVISION],
            active=True
        ).order_by('display_order', 'name'),
        empty_label="Select Department / Division",
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_department'}),
        required=False,
        label="Department / Division"
    )
    unit = forms.ModelChoiceField(
        queryset=FunctionalUnit.objects.filter(
            unit_type=FunctionalUnit.FunctionalUnitType.UNIT,
            active=True
        ).order_by('display_order', 'name'),
        empty_label="Select Unit",
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_unit'}),
        required=False,
        label="Unit"
    )

    create_user_account = forms.BooleanField(
        required=False,
        initial=True,
        label="Create Login Account?",
        help_text="Automatically provision a Staff ID user account with initial temporary password.",
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input', 'id': 'id_create_user_account'})
    )

    class Meta:
        model = NorminalRoll
        fields = [
            'staffID', 'fullname', 'gender', 'officer_category', 'rank', 'job_grade', 'birth_date', 'ghana_card',
            'station', 'post', 'functional_unit', 'mgmt_units', 'bank', 'account_number', 'payroll_status',
            'leave_type', 'leave_status', 'start_leave', 'end_leave', 'called_off'
        ]
        widgets = {
            'staffID': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. 18693'}),
            'fullname': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Officer full name'}),
            'gender': forms.Select(attrs={'class': 'form-select', 'id': 'id_gender'}),
            'job_grade': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. DOIII, ADOI, FIREMAN'}),
            'ghana_card': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'GHA-123456789-0'}),
            'mgmt_units': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Management Unit'}),
            'bank': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Bank name'}),
            'account_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Bank account number'}),
            'payroll_status': forms.Select(attrs={'class': 'form-select'}),
            'leave_type': forms.Select(attrs={'class': 'form-select'}),
            'leave_status': forms.Select(attrs={'class': 'form-select'}),
            'station': forms.Select(attrs={'class': 'form-select', 'id': 'id_station'}),
            'post': forms.Select(attrs={'class': 'form-select', 'id': 'id_post'}),
            'called_off': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        self.fields['job_grade'].required = False
        self.fields['job_grade'].widget = forms.HiddenInput()
        self.fields['functional_unit'].widget = forms.HiddenInput()
        self.fields['functional_unit'].required = False

        if user:
            self.fields['station'].queryset = get_stations_queryset_for_user(user)
            self.fields['post'].queryset = get_posts_queryset_for_user(user)
        else:
            self.fields['station'].queryset = Station.objects.filter(active=True)
            self.fields['post'].queryset = Post.objects.filter(active=True)

        self.fields['station'].empty_label = "Select Assigned Fire Station"
        self.fields['post'].empty_label = "Select Sub-Post / Market Post (Optional)"

        # Handle dynamic rank filtering based on instance or submitted data
        selected_cat = None
        if self.is_bound:
            selected_cat = self.data.get('officer_category')
        elif self.instance and self.instance.pk:
            selected_cat = self.instance.officer_category or (self.instance.rank.officer_category if self.instance.rank else None)
            if selected_cat:
                self.fields['officer_category'].initial = selected_cat

        if selected_cat in (Rank.OfficerCategory.SENIOR, Rank.OfficerCategory.JUNIOR):
            self.fields['rank'].queryset = Rank.objects.filter(officer_category=selected_cat, active=True).order_by('hierarchy_order')
        else:
            self.fields['rank'].queryset = Rank.objects.filter(active=True).order_by('hierarchy_order')

        # Preselect functional hierarchy if instance has functional_unit
        if self.instance and self.instance.pk and self.instance.functional_unit:
            fu = self.instance.functional_unit
            if fu.unit_type == FunctionalUnit.FunctionalUnitType.DIRECTORATE:
                self.fields['directorate'].initial = fu
            elif fu.unit_type in (FunctionalUnit.FunctionalUnitType.DEPARTMENT, FunctionalUnit.FunctionalUnitType.DIVISION):
                self.fields['department'].initial = fu
                self.fields['directorate'].initial = fu.directorate
            elif fu.unit_type == FunctionalUnit.FunctionalUnitType.UNIT:
                self.fields['unit'].initial = fu
                self.fields['department'].initial = fu.department_or_division
                self.fields['directorate'].initial = fu.directorate

        # Adjust cascading querysets based on selections
        selected_dir = self.data.get('directorate') or (self.fields['directorate'].initial if not self.is_bound else None)
        selected_dept = self.data.get('department') or (self.fields['department'].initial if not self.is_bound else None)

        if selected_dir:
            dir_id = selected_dir.id if hasattr(selected_dir, 'id') else selected_dir
            if dir_id:
                self.fields['department'].queryset = FunctionalUnit.objects.filter(
                    parent_id=dir_id, active=True
                ).order_by('display_order', 'name')
        if selected_dept:
            dept_id = selected_dept.id if hasattr(selected_dept, 'id') else selected_dept
            if dept_id:
                self.fields['unit'].queryset = FunctionalUnit.objects.filter(
                    parent_id=dept_id, active=True
                ).order_by('display_order', 'name')

    def clean(self):
        cleaned_data = super().clean()
        called_off = cleaned_data.get("called_off")

        if called_off:
            cleaned_data["leave_type"] = "AT POST"
            cleaned_data["leave_status"] = "AT POST"
            cleaned_data["start_leave"] = None
            cleaned_data["end_leave"] = None

        station = cleaned_data.get("station")
        post = cleaned_data.get("post")

        if post and station and post.station != station:
            self.add_error("post", f"Post '{post.name}' does not belong to station '{station.name}'.")

        # Validate Officer Category and Rank compatibility
        officer_category = cleaned_data.get("officer_category")
        rank = cleaned_data.get("rank")

        if rank and officer_category:
            if rank.officer_category != officer_category:
                cat_display = "Senior Officer" if officer_category == Rank.OfficerCategory.SENIOR else "Junior Officer"
                rank_cat_display = rank.get_officer_category_display()
                self.add_error("rank", f"Invalid combination: Rank '{rank.name}' is a {rank_cat_display}, but Officer Category was set to {cat_display}.")
            else:
                cleaned_data["job_grade"] = rank.abbreviation
        elif rank and not officer_category:
            cleaned_data["officer_category"] = rank.officer_category
            cleaned_data["job_grade"] = rank.abbreviation

        # Functional hierarchy validation
        directorate = cleaned_data.get('directorate')
        department = cleaned_data.get('department')
        unit = cleaned_data.get('unit')

        if department and directorate:
            if department.parent != directorate and department.directorate != directorate:
                self.add_error('department', f"Department '{department.name}' does not belong to Directorate '{directorate.name}'.")
        if unit and department:
            if unit.parent != department and unit.department_or_division != department:
                self.add_error('unit', f"Unit '{unit.name}' does not belong to Department '{department.name}'.")

        # Resolve effective functional unit (lowest chosen level)
        eff_func_unit = unit or department or directorate
        cleaned_data['functional_unit'] = eff_func_unit

        return cleaned_data


class ArchiveRequestForm(forms.Form):
    reason = forms.CharField(
        widget=forms.Textarea(attrs={
            'rows': 4,
            'class': 'form-control',
            'placeholder': 'Provide official justification and documentation details for archiving this personnel record...'
        }),
        required=True,
        label="Archival Justification & Operational Reason"
    )


class ApprovalDecisionForm(forms.Form):
    decision = forms.ChoiceField(
        choices=[('APPROVED', 'Approve Request'), ('REJECTED', 'Reject Request')],
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'}),
        label="Approval Decision"
    )
    comment = forms.CharField(
        widget=forms.Textarea(attrs={
            'rows': 3,
            'class': 'form-control',
            'placeholder': 'Optional comments, administrative instructions, or rejection reasons...'
        }),
        required=False,
        label="Administrative Comments"
    )


# ==============================================================================
# STRUCTURE MANAGEMENT FORMS (REGIONS, DISTRICTS, STATIONS, POSTS)
# ==============================================================================

class RegionForm(forms.ModelForm):
    class Meta:
        model = Region
        fields = ['name', 'code', 'active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Greater Accra Region'}),
            'code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. GAR'}),
            'active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean_name(self):
        return self.cleaned_data.get('name', '').strip()

    def clean_code(self):
        return self.cleaned_data.get('code', '').strip().upper()


class DistrictForm(forms.ModelForm):
    class Meta:
        model = District
        fields = ['region', 'name', 'code', 'active']
        widgets = {
            'region': forms.Select(attrs={'class': 'form-select'}),
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Komenda-Edina-Eguafo-Abirem Municipal'}),
            'code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. CR-KEEA (Optional)'}),
            'active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['region'].queryset = Region.objects.filter(active=True).order_name() if hasattr(Region.objects, 'order_name') else Region.objects.filter(active=True).order_by('name')
        self.fields['region'].empty_label = "Select Parent Region"

    def clean_name(self):
        return self.cleaned_data.get('name', '').strip()

    def clean_code(self):
        return (self.cleaned_data.get('code') or '').strip().upper()


class StationForm(forms.ModelForm):
    class Meta:
        model = Station
        fields = ['district', 'name', 'code', 'station_type', 'active']
        widgets = {
            'district': forms.Select(attrs={'class': 'form-select'}),
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Komenda Fire Station'}),
            'code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. CR-KEEA-KOM (Optional)'}),
            'station_type': forms.Select(attrs={'class': 'form-select'}),
            'active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['district'].queryset = District.objects.filter(active=True).select_related('region').order_by('region__name', 'name')
        self.fields['district'].empty_label = "Select Parent District Command"

    def clean_name(self):
        return self.cleaned_data.get('name', '').strip()

    def clean_code(self):
        return (self.cleaned_data.get('code') or '').strip().upper()


class PostForm(forms.ModelForm):
    class Meta:
        model = Post
        fields = ['station', 'name', 'code', 'post_type', 'active']
        widgets = {
            'station': forms.Select(attrs={'class': 'form-select'}),
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Komenda Market Fire Post'}),
            'code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. CR-KOM-P01 (Optional)'}),
            'post_type': forms.Select(attrs={'class': 'form-select'}),
            'active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['station'].queryset = Station.objects.filter(active=True).select_related('district__region').order_by('district__name', 'name')
        self.fields['station'].empty_label = "Select Parent Fire Station"

    def clean_name(self):
        return self.cleaned_data.get('name', '').strip()

    def clean_code(self):
        return (self.cleaned_data.get('code') or '').strip().upper()


# ==============================================================================
# ENTERPRISE LEAVE MANAGEMENT FORMS
# ==============================================================================

from gnfs_komenda.models import LeaveRequest, LeaveType, LeaveDefermentRequest, PublicHoliday, LeaveEntitlement
from gnfs_komenda.security import get_personnel_queryset_for_user


class LeaveRequestForm(forms.ModelForm):
    start_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control', 'id': 'id_start_date'}),
        required=True,
        label="Start Date"
    )
    duration = forms.IntegerField(
        widget=forms.NumberInput(attrs={'class': 'form-control', 'id': 'id_duration', 'min': 1}),
        required=True,
        label="Duration"
    )
    is_proportionate = forms.BooleanField(
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input', 'id': 'id_is_proportionate'}),
        required=False,
        label="Proportionate / Part Leave"
    )

    class Meta:
        model = LeaveRequest
        fields = [
            'personnel', 'leave_type', 'is_proportionate', 'start_date', 'duration',
            'medical_reference', 'attachment', 'reason',
            'contact_phone', 'contact_address', 'handover_notes'
        ]
        widgets = {
            'personnel': forms.Select(attrs={'class': 'form-select select2', 'id': 'id_personnel'}),
            'leave_type': forms.Select(attrs={'class': 'form-select', 'id': 'id_leave_type'}),
            'is_proportionate': forms.CheckboxInput(attrs={'class': 'form-check-input', 'id': 'id_is_proportionate'}),
            'medical_reference': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. 37 Military Hospital - Dr. Mensah'}),
            'attachment': forms.FileInput(attrs={'class': 'form-control', 'id': 'id_attachment'}),
            'reason': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Reason for leave application...'}),
            'contact_phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Emergency phone number during leave'}),
            'contact_address': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Residential/contact address during leave'}),
            'handover_notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Brief description of duties/gear handed over...'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user:
            self.fields['personnel'].queryset = get_personnel_queryset_for_user(user).order_by('fullname')
        else:
            self.fields['personnel'].queryset = NorminalRoll.active_objects.all().order_by('fullname')
        self.fields['leave_type'].queryset = LeaveType.objects.filter(active=True).order_by('name')

    def clean(self):
        cleaned_data = super().clean()
        personnel = cleaned_data.get('personnel')
        leave_type = cleaned_data.get('leave_type')
        medical_ref = cleaned_data.get('medical_reference')
        attachment = cleaned_data.get('attachment')

        if personnel and leave_type:
            # Gender restriction validation
            if hasattr(leave_type, 'allowed_gender'):
                if leave_type.allowed_gender == LeaveType.AllowedGender.MALE and getattr(personnel, 'gender', 'MALE') != NorminalRoll.Gender.MALE:
                    self.add_error('leave_type', "Female personnel cannot apply for Paternity Leave. Female personnel are entitled to Maternity Leave.")
                elif leave_type.allowed_gender == LeaveType.AllowedGender.FEMALE and getattr(personnel, 'gender', 'MALE') != NorminalRoll.Gender.FEMALE:
                    self.add_error('leave_type', "Male personnel cannot apply for Maternity Leave. Male personnel are entitled to Paternity Leave (5 working days).")

            # Sick Leave validation:
            if leave_type.code == 'SICK':
                if not medical_ref and not attachment:
                    self.add_error('medical_reference', "Please provide the Hospital/Clinic name or upload the official Excuse Duty letter.")

        return cleaned_data


class LeaveDefermentForm(forms.ModelForm):
    class Meta:
        model = LeaveDefermentRequest
        fields = ['personnel', 'leave_year', 'days_requested', 'reason']
        widgets = {
            'personnel': forms.Select(attrs={'class': 'form-select', 'id': 'id_deferment_personnel'}),
            'leave_year': forms.NumberInput(attrs={'class': 'form-control', 'id': 'id_leave_year'}),
            'days_requested': forms.NumberInput(attrs={'class': 'form-control', 'id': 'id_days_requested', 'min': 1}),
            'reason': forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 'placeholder': 'Official operational justification for deferring remaining leave...'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user:
            self.fields['personnel'].queryset = get_personnel_queryset_for_user(user).order_by('fullname')
        else:
            self.fields['personnel'].queryset = NorminalRoll.active_objects.all().order_by('fullname')


class LeaveRecallForm(forms.Form):
    recall_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        required=True,
        label="Official Recall Date"
    )
    recall_reason = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Operational exigency or emergency requiring officer recall...'}),
        required=True,
        label="Reason for Recall"
    )


class LeaveAdjustmentForm(forms.Form):
    days_adjusted = forms.IntegerField(
        widget=forms.NumberInput(attrs={'class': 'form-control'}),
        required=True,
        help_text="Positive number to credit days, negative to debit days",
        label="Days to Adjust (+/-)"
    )
    reason = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Official reason/memo reference for balance adjustment...'}),
        required=True,
        label="Adjustment Justification"
    )


class ProportionateLeaveEntitlementForm(forms.Form):
    class EntitlementType(models.TextChoices):
        STANDARD = "STANDARD", "Standard Full Entitlement"
        PROPORTIONATE = "PROPORTIONATE", "Proportionate / Prorated"

    entitlement_type = forms.ChoiceField(
        choices=EntitlementType.choices,
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_entitlement_type'}),
        required=True,
        label="Entitlement Type *"
    )
    proportionate_days = forms.IntegerField(
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'id': 'id_proportionate_days',
            'min': 1,
            'placeholder': 'e.g. 18'
        }),
        required=False,
        label="Proportionate Days *"
    )
    reason = forms.CharField(
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'id': 'id_proration_reason',
            'rows': 3,
            'placeholder': 'Official operational justification or memo for proportionate annual leave entitlement...'
        }),
        required=False,
        label="Reason *"
    )
    effective_date = forms.DateField(
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control',
            'id': 'id_proration_effective_date'
        }),
        required=False,
        label="Effective Date / Period"
    )

    def __init__(self, *args, entitlement=None, user=None, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        from gnfs_komenda.services.leave_policy_service import get_base_annual_entitlement
        self.entitlement = entitlement
        self.user = user or actor
        self.actor = actor or user

        if entitlement:
            standard_days = entitlement.full_annual_entitlement or get_base_annual_entitlement(entitlement.personnel.officer_category)
            if entitlement.is_prorated:
                self.fields['entitlement_type'].initial = self.EntitlementType.PROPORTIONATE
                self.fields['proportionate_days'].initial = entitlement.base_entitlement
                self.fields['reason'].initial = entitlement.proration_reason
            else:
                self.fields['entitlement_type'].initial = self.EntitlementType.STANDARD
                self.fields['proportionate_days'].initial = standard_days

    def clean(self):
        cleaned_data = super().clean()
        ent_type = cleaned_data.get('entitlement_type')
        days = cleaned_data.get('proportionate_days')
        reason = (cleaned_data.get('reason') or '').strip()

        from gnfs_komenda.services.leave_policy_service import can_manage_proportionate_entitlement, get_base_annual_entitlement

        if self.user and self.entitlement:
            if not can_manage_proportionate_entitlement(self.user, self.entitlement.personnel):
                raise forms.ValidationError("You do not have administrative authority to configure this personnel's leave entitlement.")

        if ent_type == self.EntitlementType.PROPORTIONATE:
            if days is None:
                self.add_error('proportionate_days', "Proportionate days is required when Proportionate / Prorated is selected.")
            elif days <= 0:
                self.add_error('proportionate_days', "Proportionate days must be greater than zero.")
            else:
                if self.entitlement:
                    standard_days = self.entitlement.full_annual_entitlement or get_base_annual_entitlement(self.entitlement.personnel.officer_category)
                    if days > standard_days:
                        self.add_error('proportionate_days', f"Proportionate days ({days}) cannot exceed the standard full annual entitlement ({standard_days} working days).")

            if not reason:
                self.add_error('reason', "Reason is mandatory when recording a proportionate / prorated annual entitlement.")

        return cleaned_data


class PublicHolidayForm(forms.ModelForm):
    date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        required=True
    )

    class Meta:
        model = PublicHoliday
        fields = ['name', 'date', 'active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Holiday name'}),
            'active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


# ==============================================================================
# PERSONNEL TRANSFER & FUNCTIONAL ROLE ASSIGNMENT FORMS
# ==============================================================================

class PersonnelTransferForm(forms.ModelForm):
    destination_region = forms.ModelChoiceField(
        queryset=Region.objects.filter(active=True).order_by('name'),
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_trf_destination_region'}),
        required=True,
        label="Destination Region *"
    )
    destination_district = forms.ModelChoiceField(
        queryset=District.objects.filter(active=True).order_by('name'),
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_trf_destination_district'}),
        required=False,
        label="Destination District"
    )
    destination_station = forms.ModelChoiceField(
        queryset=Station.objects.filter(active=True).order_by('name'),
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_trf_destination_station'}),
        required=False,
        label="Destination Fire Station"
    )
    destination_post = forms.ModelChoiceField(
        queryset=Post.objects.filter(active=True).order_by('name'),
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_trf_destination_post'}),
        required=False,
        label="Destination Fire Post (Optional)"
    )
    destination_functional_unit = forms.ModelChoiceField(
        queryset=FunctionalUnit.objects.filter(active=True).order_by('display_order', 'name'),
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_trf_destination_functional_unit'}),
        required=False,
        label="Destination Functional Assignment (Optional)"
    )
    effective_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control', 'id': 'id_trf_effective_date'}),
        required=True,
        label="Effective Transfer Date *"
    )
    expected_reporting_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control', 'id': 'id_trf_expected_reporting_date'}),
        required=True,
        label="Expected Reporting Date *"
    )
    transfer_letter_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        required=False,
        label="Transfer Letter Date"
    )

    class Meta:
        model = PersonnelTransfer
        fields = [
            'destination_region', 'destination_district', 'destination_station', 'destination_post',
            'destination_functional_unit', 'transfer_letter_number', 'transfer_letter_date',
            'effective_date', 'expected_reporting_date', 'reason', 'remarks', 'attachment'
        ]
        widgets = {
            'transfer_letter_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. GNFS/HQ/TRF/2026/042'}),
            'reason': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Operational requirement, rotation, specialist posting, etc.'}),
            'remarks': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Additional administrative instructions or routing notes...'}),
            'attachment': forms.FileInput(attrs={'class': 'form-control'}),
        }


class TransferDestinationPlacementForm(forms.ModelForm):
    destination_district = forms.ModelChoiceField(
        queryset=District.objects.filter(active=True).order_by('name'),
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_place_district'}),
        required=True,
        label="Assigned District *"
    )
    destination_station = forms.ModelChoiceField(
        queryset=Station.objects.filter(active=True).order_by('name'),
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_place_station'}),
        required=True,
        label="Assigned Fire Station *"
    )
    destination_post = forms.ModelChoiceField(
        queryset=Post.objects.filter(active=True).order_by('name'),
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_place_post'}),
        required=False,
        label="Assigned Fire Post (Optional)"
    )

    class Meta:
        model = PersonnelTransfer
        fields = ['destination_district', 'destination_station', 'destination_post', 'remarks']
        widgets = {
            'remarks': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Placement memo reference or station notes...'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.destination_region:
            reg = self.instance.destination_region
            self.fields['destination_district'].queryset = District.objects.filter(region=reg, active=True).order_by('name')
            self.fields['destination_station'].queryset = Station.objects.filter(district__region=reg, active=True).order_by('name')


class TransferConfirmArrivalForm(forms.Form):
    actual_reporting_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        required=True,
        label="Actual Reporting Date *"
    )
    remarks = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Arrival confirmation notes, duty assumption memo, kit inspection...'}),
        required=False,
        label="Confirmation Remarks"
    )


class TransferCancelForm(forms.Form):
    cancellation_reason = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Provide official reason for cancelling this transfer...'}),
        required=True,
        label="Reason for Cancellation *"
    )


class FunctionalRoleAssignmentForm(forms.ModelForm):
    class Meta:
        model = FunctionalRoleAssignment
        fields = ['user', 'organisational_unit', 'role', 'active', 'effective_from', 'effective_to']
        widgets = {
            'user': forms.Select(attrs={'class': 'form-select'}),
            'organisational_unit': forms.Select(attrs={'class': 'form-select'}),
            'role': forms.Select(attrs={'class': 'form-select'}),
            'active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'effective_from': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'effective_to': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        }


# ==============================================================================
# SECTION 17: COMMAND APPOINTMENT & LEADERSHIP MANAGEMENT FORMS
# ==============================================================================

class RegionLeadershipForm(forms.Form):
    rfo = forms.ModelChoiceField(
        queryset=NorminalRoll.objects.filter(is_active=True).order_by('fullname'),
        required=False,
        empty_label="-- Select Regional Fire Officer (RFO) --",
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_rfo'}),
        label="Regional Fire Officer (RFO)"
    )
    rfo_is_acting = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input', 'id': 'id_rfo_is_acting'}),
        label="Acting RFO Appointment"
    )
    regional_2ic = forms.ModelChoiceField(
        queryset=NorminalRoll.objects.filter(is_active=True).order_by('fullname'),
        required=False,
        empty_label="-- Select Regional Second-in-Command (2IC) --",
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_regional_2ic'}),
        label="Regional Second-in-Command (2IC)"
    )
    regional_2ic_is_acting = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input', 'id': 'id_regional_2ic_is_acting'}),
        label="Acting Regional 2IC Appointment"
    )
    effective_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control', 'id': 'id_effective_date'}),
        required=True,
        label="Effective Date *"
    )
    appointment_reference = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. HQ/OPS/APPT/2026/04'}),
        label="Appointment Reference Letter / Order Number"
    )
    remarks = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Appointment order remarks, handover details...'}),
        label="Remarks / Handover Notes"
    )

    def __init__(self, *args, region=None, **kwargs):
        super().__init__(*args, **kwargs)
        if region:
            # Show personnel posted in the region first, or all personnel
            pass


class DistrictLeadershipForm(forms.Form):
    command_type = forms.ChoiceField(
        choices=District.CommandType.choices,
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_command_type'}),
        required=True,
        label="Command Type *"
    )
    co_designation = forms.ChoiceField(
        choices=[
            ('DFO', 'District Fire Officer (DFO)'),
            ('MFO', 'Municipal Fire Officer (MFO)'),
            ('METRO_FO', 'Metropolitan Fire Officer (MFO)'),
        ],
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_co_designation'}),
        required=True,
        label="Officer-in-Charge Designation *"
    )
    commanding_officer = forms.ModelChoiceField(
        queryset=NorminalRoll.objects.filter(is_active=True).order_by('fullname'),
        required=False,
        empty_label="-- Select Officer-in-Charge (DFO / MFO) --",
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_commanding_officer'}),
        label="Commanding Officer (DFO / MFO)"
    )
    co_is_acting = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input', 'id': 'id_co_is_acting'}),
        label="Acting Commanding Officer Appointment"
    )
    district_2ic = forms.ModelChoiceField(
        queryset=NorminalRoll.objects.filter(is_active=True).order_by('fullname'),
        required=False,
        empty_label="-- Select Second-in-Command (2IC) --",
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_district_2ic'}),
        label="Second-in-Command (2IC)"
    )
    district_2ic_is_acting = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input', 'id': 'id_district_2ic_is_acting'}),
        label="Acting District 2IC Appointment"
    )
    effective_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control', 'id': 'id_effective_date'}),
        required=True,
        label="Effective Date *"
    )
    appointment_reference = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. REG/CR/APPT/2026/08'}),
        label="Appointment Reference"
    )
    remarks = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Leadership appointment remarks...'}),
        label="Remarks / Handover Notes"
    )


# ==============================================================================
# SECTION 17: ORGANISATIONAL STRUCTURE CRUD FORMS
# ==============================================================================

class DirectorateForm(forms.ModelForm):
    class Meta:
        model = FunctionalUnit
        fields = ['name', 'code', 'description', 'display_order', 'active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Administration, Operations, Fire Safety'}),
            'code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. ADMIN, OPS, FS'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Directorate mandate and functions...'}),
            'display_order': forms.NumberInput(attrs={'class': 'form-control'}),
            'active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class DepartmentForm(forms.ModelForm):
    parent = forms.ModelChoiceField(
        queryset=FunctionalUnit.objects.filter(
            unit_type=FunctionalUnit.FunctionalUnitType.DIRECTORATE,
            active=True
        ).order_by('display_order', 'name'),
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_parent_directorate'}),
        required=True,
        label="Parent Directorate *"
    )

    class Meta:
        model = FunctionalUnit
        fields = ['parent', 'name', 'code', 'description', 'display_order', 'active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Public Relations, Human Resource, Training'}),
            'code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. PR, HR, TRG'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'display_order': forms.NumberInput(attrs={'class': 'form-control'}),
            'active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class UnitForm(forms.ModelForm):
    parent = forms.ModelChoiceField(
        queryset=FunctionalUnit.objects.filter(
            unit_type__in=[FunctionalUnit.FunctionalUnitType.DEPARTMENT, FunctionalUnit.FunctionalUnitType.DIVISION],
            active=True
        ).order_by('parent__name', 'name'),
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_parent_department'}),
        required=True,
        label="Parent Department / Division *"
    )

    class Meta:
        model = FunctionalUnit
        fields = ['parent', 'name', 'code', 'description', 'display_order', 'active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Public Education, Media Relations, Central Registry'}),
            'code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. PUB-ED, MEDIA, REG'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'display_order': forms.NumberInput(attrs={'class': 'form-control'}),
            'active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class PersonnelFunctionalAssignmentCreateForm(forms.Form):
    functional_unit = forms.ModelChoiceField(
        queryset=FunctionalUnit.objects.filter(active=True).order_by('name'),
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_assign_unit'}),
        required=True,
        label="Functional Directorate / Department / Unit *"
    )
    assignment_role = forms.CharField(
        initial="Member",
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Member, Coordinator, Schedule Officer'}),
        required=False,
        label="Assignment Role"
    )
    is_primary = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input', 'id': 'id_assign_is_primary'}),
        label="Mark as Primary Functional Assignment"
    )
    start_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        required=True,
        label="Effective From *"
    )
    reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Assignment purpose, posting order reference...'}),
        label="Assignment Reason / Remarks"
    )


class AdministrativeRoleAssignmentForm(forms.Form):
    """
    Hierarchical Role & Responsibility Assignment Form.
    Enforces organizational scope (Level -> Region -> District Command / Directorate -> Department -> Unit)
    prior to selecting personnel, strictly scoping candidate personnel to authoritative current posting.
    """
    LEVEL_CHOICES = [
        ('', '-- Select Organisational Level --'),
        ('NATIONAL', 'National Headquarters'),
        ('REGIONAL', 'Regional Command'),
        ('DISTRICT', 'District / Fire Station'),
    ]

    level = forms.ChoiceField(
        choices=LEVEL_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select select2-basic', 'id': 'id_level'}),
        required=True,
        label="Organisational Level *"
    )
    region = forms.ModelChoiceField(
        queryset=Region.objects.filter(active=True).order_by('name'),
        widget=forms.Select(attrs={'class': 'form-select select2-basic', 'id': 'id_region'}),
        required=False,
        empty_label="-- Select Region --",
        label="Region *"
    )
    district = forms.ModelChoiceField(
        queryset=District.objects.filter(active=True).order_by('name'),
        widget=forms.Select(attrs={'class': 'form-select select2-basic', 'id': 'id_district'}),
        required=False,
        empty_label="-- Select District --",
        label="District *"
    )
    station = forms.ModelChoiceField(
        queryset=Station.objects.filter(active=True).order_by('name'),
        widget=forms.Select(attrs={'class': 'form-select select2-basic', 'id': 'id_station'}),
        required=False,
        empty_label="-- Select Fire Station --",
        label="Fire Station *"
    )
    directorate = forms.ModelChoiceField(
        queryset=FunctionalUnit.objects.filter(unit_type=FunctionalUnit.FunctionalUnitType.DIRECTORATE, active=True).order_by('display_order', 'name'),
        widget=forms.Select(attrs={'class': 'form-select select2-basic', 'id': 'id_directorate'}),
        required=False,
        empty_label="-- Select Directorate --",
        label="Directorate *"
    )
    department = forms.CharField(
        widget=forms.Select(attrs={'class': 'form-select select2-basic', 'id': 'id_department'}),
        required=True,
        label="Department *"
    )
    unit = forms.CharField(
        widget=forms.Select(attrs={'class': 'form-select select2-basic', 'id': 'id_unit'}),
        required=False,
        label="Unit"
    )
    role = forms.CharField(
        widget=forms.Select(attrs={'class': 'form-select select2-basic', 'id': 'id_role'}),
        required=True,
        label="Role / Responsibility *"
    )
    personnel = forms.ModelChoiceField(
        queryset=NorminalRoll.active_objects.all(),
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_personnel'}),
        required=True,
        label="Personnel Officer *"
    )
    effective_from = forms.DateField(
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control', 'id': 'id_effective_from'}),
        required=True,
        label="Effective Date *"
    )
    remarks = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Appointment order reference, notes, remarks...', 'id': 'id_remarks'}),
        label="Remarks / Appointment Details"
    )

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        from gnfs_komenda.services.role_service import get_actor_administrative_scope

        self.actor = actor
        actor_scope = get_actor_administrative_scope(actor) if actor else {"level": "NONE"}

        if actor_scope["level"] == "REGIONAL":
            self.fields['level'].choices = [
                ('', '-- Select Organisational Level --'),
                ('REGIONAL', 'Regional Command'),
                ('DISTRICT', 'District / Fire Station'),
            ]
            reg = actor_scope["region"]
            self.fields['region'].queryset = Region.objects.filter(id=reg.id)
            self.fields['region'].initial = reg
            self.fields['district'].queryset = District.objects.filter(region=reg, active=True).order_by('name')
            self.fields['station'].queryset = Station.objects.filter(district__region=reg, active=True).order_by('name')
        elif actor_scope["level"] == "DISTRICT":
            self.fields['level'].choices = [
                ('DISTRICT', 'District / Fire Station'),
            ]
            self.fields['level'].initial = 'DISTRICT'
            dist = actor_scope["district"]
            reg = actor_scope["region"] or (dist.region if dist else None)
            if reg:
                self.fields['region'].queryset = Region.objects.filter(id=reg.id)
                self.fields['region'].initial = reg
            if dist:
                self.fields['district'].queryset = District.objects.filter(id=dist.id)
                self.fields['district'].initial = dist
                self.fields['station'].queryset = Station.objects.filter(district=dist, active=True).order_by('name')

    def clean(self):
        cleaned_data = super().clean()
        level = cleaned_data.get('level')
        region = cleaned_data.get('region')
        district = cleaned_data.get('district')
        station = cleaned_data.get('station')
        directorate = cleaned_data.get('directorate')
        department = cleaned_data.get('department')
        unit = cleaned_data.get('unit')
        role = cleaned_data.get('role')
        personnel = cleaned_data.get('personnel')

        from gnfs_komenda.models import FunctionalOffice, FunctionalUnit, PersonnelPosting, AdministrativeAssignment, ApplicationRole
        from gnfs_komenda.services.role_service import get_actor_administrative_scope, can_assign_role

        actor_scope = get_actor_administrative_scope(self.actor) if self.actor else {"level": "NONE"}

        # Level-based validation
        if level == 'NATIONAL':
            if not directorate:
                self.add_error('directorate', "Directorate is required for National scope.")
            cleaned_data['region'] = None
            cleaned_data['district'] = None
            cleaned_data['station'] = None
            if actor_scope["level"] != "NATIONAL":
                raise forms.ValidationError("You are not authorized to assign roles at the National level.")

        elif level == 'REGIONAL':
            if directorate:
                self.add_error('directorate', "Directorate is only applicable at National level.")
            if not region:
                self.add_error('region', "Region is required for Regional scope.")
            if district:
                self.add_error('district', "District cannot be selected for Regional scope.")
            if station:
                self.add_error('station', "Fire Station cannot be selected for Regional scope.")
            cleaned_data['directorate'] = None
            cleaned_data['district'] = None
            cleaned_data['station'] = None
            if actor_scope["level"] == "REGIONAL" and region and actor_scope["region"] and region.id != actor_scope["region"].id:
                raise forms.ValidationError("Regional Administrators can only assign roles within their assigned Region.")
            if actor_scope["level"] == "DISTRICT":
                raise forms.ValidationError("District Administrators cannot assign roles at the Regional level.")

        elif level == 'DISTRICT':
            if directorate:
                self.add_error('directorate', "Directorate is only applicable at National level.")
            if not region:
                self.add_error('region', "Region is required for District scope.")
            if not district:
                self.add_error('district', "District is required.")
            elif region and district.region_id != region.id:
                self.add_error('district', f"District '{district.name}' does not belong to '{region.name}'.")
            if not station:
                self.add_error('station', "Fire Station is required.")
            elif district and station.district_id != district.id:
                self.add_error('station', f"Fire Station '{station.name}' does not belong to District '{district.name}'.")
            cleaned_data['directorate'] = None

            if actor_scope["level"] == "REGIONAL" and region and actor_scope["region"] and region.id != actor_scope["region"].id:
                raise forms.ValidationError("Regional Administrators can only assign roles within their assigned Region.")
            if actor_scope["level"] == "DISTRICT" and district and actor_scope["district"] and district.id != actor_scope["district"].id:
                raise forms.ValidationError("District Administrators can only assign roles within their assigned District.")

            # Validate Department established status (e.g. HR fallback)
            if district and department:
                dept_upper = str(department).upper()
                if dept_upper in ("HR", "HUMAN RESOURCE", "HUMAN RESOURCES"):
                    hr_office = FunctionalOffice.objects.filter(
                        command_level=FunctionalOffice.CommandLevel.DISTRICT,
                        district=district,
                        function_type=FunctionalOffice.FunctionType.HR,
                        active=True
                    ).first()
                    if hr_office and not hr_office.is_established:
                        raise forms.ValidationError(
                            f"Human Resource department is not established for {district.name}. "
                            f"HR functions fall back to Administration at this command."
                        )

        # Validate Personnel eligibility
        if personnel:
            if not personnel.is_active:
                self.add_error('personnel', f"Personnel {personnel.fullname} is inactive and cannot receive role assignments.")

            # Anti-self-promotion
            actor_personnel = getattr(self.actor, 'assignment', None)
            if actor_personnel and actor_personnel.personnel_id == personnel.id:
                self.add_error('personnel', "You cannot assign an administrative role or responsibility to yourself.")

            # Scope constraint check
            if level == 'DISTRICT':
                if station:
                    in_station = (
                        (personnel.station_id == station.id) or
                        PersonnelPosting.objects.filter(personnel=personnel, is_current=True, station=station).exists()
                    )
                    if not in_station:
                        self.add_error('personnel', f"Officer {personnel.fullname} ({personnel.staffID}) is not currently posted to {station.name}.")
                elif district:
                    in_district = (
                        (personnel.station and personnel.station.district_id == district.id) or
                        PersonnelPosting.objects.filter(personnel=personnel, is_current=True, district=district).exists()
                    )
                    if not in_district:
                        self.add_error('personnel', f"Officer {personnel.fullname} ({personnel.staffID}) is not currently posted to {district.name}.")
            elif level == 'REGIONAL' and region:
                in_region = (
                    (personnel.station and personnel.station.district and personnel.station.district.region_id == region.id) or
                    PersonnelPosting.objects.filter(personnel=personnel, is_current=True, region=region).exists()
                )
                if not in_region:
                    self.add_error('personnel', f"Officer {personnel.fullname} ({personnel.staffID}) is not currently posted to {region.name}.")

        # Duplicate active assignment prevention
        if personnel and role:
            app_role = ApplicationRole.objects.filter(Q(code=role) | Q(name=role)).first()
            if app_role:
                dup = AdministrativeAssignment.objects.filter(
                    personnel=personnel,
                    role=app_role,
                    region=cleaned_data.get('region'),
                    district=cleaned_data.get('district'),
                    station=cleaned_data.get('station'),
                    is_active=True
                ).exists()
            else:
                dup = AdministrativeAssignment.objects.filter(
                    personnel=personnel,
                    assignment_type=AdministrativeAssignment.AssignmentType.FUNCTIONAL,
                    responsibility_title=role,
                    level=level,
                    region=cleaned_data.get('region'),
                    district=cleaned_data.get('district'),
                    station=cleaned_data.get('station'),
                    is_active=True
                ).exists()

            if dup:
                self.add_error('role', f"Officer {personnel.fullname} already has an active assignment for '{role}' in this scope.")

        return cleaned_data


class AdministrativeRoleRevocationForm(forms.Form):
    """Form to confirm administrative role revocation with mandatory reason (Section 10, 17, 64)."""
    reason = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 3,
            'placeholder': 'Provide official reason for revoking this administrative assignment...',
            'required': 'required'
        }),
        label="Reason for Revocation *"
    )




