from django import forms
from gnfs_komenda.models import Region, District, Station, Post, NorminalRoll, Rank
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

    class Meta:
        model = NorminalRoll
        fields = [
            'staffID', 'fullname', 'gender', 'officer_category', 'rank', 'job_grade', 'birth_date', 'ghana_card',
            'station', 'post', 'mgmt_units', 'bank', 'account_number', 'payroll_status',
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

