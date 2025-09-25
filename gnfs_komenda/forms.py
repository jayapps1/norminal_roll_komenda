from django import forms
from gnfs_komenda.models import NorminalRoll

class NorminalRollForm(forms.ModelForm):
    birth_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}),
        input_formats=['%Y-%m-%d'],
        required=False
    )
    start_leave = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}),
        input_formats=['%Y-%m-%d'],
        required=False
    )
    end_leave = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}),
        input_formats=['%Y-%m-%d'],
        required=False
    )

    class Meta:
        model = NorminalRoll
        fields = [
            'staffID', 'fullname', 'job_grade', 'birth_date', 'ghana_card',
            'mgmt_units', 'bank', 'account_number', 'payroll_status',
            'leave_type', 'leave_status', 'start_leave', 'end_leave', 'called_off'
        ]

    def clean(self):
        cleaned_data = super().clean()
        called_off = cleaned_data.get("called_off")

        if called_off:
            # UI sync → force AT POST
            cleaned_data["leave_type"] = "AT POST"
            cleaned_data["leave_status"] = "AT POST"
            cleaned_data["start_leave"] = None
            cleaned_data["end_leave"] = None

        # if not called_off → let model.save() handle leave_status automatically
        return cleaned_data
