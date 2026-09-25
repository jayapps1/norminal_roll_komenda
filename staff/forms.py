from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from django import forms


# Create your models here.
class RegisterStaffForm(UserCreationForm):
    first_name = forms.CharField(max_length=100, required=True)
    last_name = forms.CharField(max_length=150, required=True)
    email = forms.EmailField(required=False)

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'username', 'email', 'password1', 'password2']


class TOTPVerifyForm(forms.Form):
    token = forms.CharField(
        max_length=8,
        min_length=6,
        required=True,
        label="6-Digit Verification Code",
        widget=forms.TextInput(attrs={
            "class": "form-control text-center fs-4 letter-spacing-2",
            "placeholder": "123456",
            "autocomplete": "one-time-code",
            "inputmode": "numeric",
            "maxlength": "6",
            "autofocus": "autofocus",
        })
    )


class ForgotPasswordRequestForm(forms.Form):
    identity = forms.CharField(
        max_length=150,
        required=True,
        label="Username or Email",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Enter your username or email address",
            "autofocus": "autofocus",
        })
    )


class ProfileUpdateForm(forms.ModelForm):
    first_name = forms.CharField(
        max_length=150,
        required=False,
        label="First Name",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Enter first name"}),
    )
    last_name = forms.CharField(
        max_length=150,
        required=False,
        label="Last Name",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Enter last name"}),
    )
    email = forms.EmailField(
        required=False,
        label="Email Address",
        widget=forms.EmailInput(attrs={"class": "form-control", "placeholder": "name@example.com"}),
    )
    username = forms.CharField(
        max_length=150,
        required=True,
        label="Username",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Enter username"}),
    )

    class Meta:
        model = User
        fields = ["first_name", "last_name", "username", "email"]

    def clean_username(self):
        username = self.cleaned_data.get("username", "").strip()
        if not username:
            raise forms.ValidationError("Username cannot be empty.")
        exists = User.objects.filter(username__iexact=username).exclude(pk=self.instance.pk).exists()
        if exists:
            raise forms.ValidationError("This username is already taken. Please choose another.")
        return username

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip()
        if email:
            exists = User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists()
            if exists:
                raise forms.ValidationError("This email is already associated with another account.")
        return email


