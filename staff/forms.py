from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from django import forms


# Create your models here.
class RegisterStaffForm(UserCreationForm):
    first_name = forms.CharField(max_length=100, required=True)
    last_name = forms.CharField(max_length=150, required=True)
    email = forms.EmailField( required=False)

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'username', 'email', 'password1', 'password2']

