from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, SetPasswordForm
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from staff.forms import ForgotPasswordRequestForm, ProfileUpdateForm, RegisterStaffForm, TOTPVerifyForm
from staff.models import StaffTOTPDevice

User = get_user_model()


def registerstaff(request):
    if request.method == 'POST':
        form = RegisterStaffForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)  # login only once
            messages.success(request, "Staff added and logged in successfully.")
            return redirect('personnel')
        else:
            messages.error(request, "Kindly correct the errors.")
    else:
        form = RegisterStaffForm()
    return render(request, 'staff/register-staff.html', {'form': form})


def stafflogin(request):
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()  # ✅ cleaner
            login(request, user)
            messages.success(request, "Login successful!")
            return redirect("personnel")
        else:
            messages.error(request, "Invalid username or password.")
    else:
        form = AuthenticationForm()
    return render(request, "staff/staff-login.html", {"form": form})


def stafflogout(request):
    logout(request)
    messages.success(request, "You have been logged out.")
    return redirect('login-staff')


@login_required(login_url='login-staff')
def totp_setup(request):
    device, _ = StaffTOTPDevice.objects.get_or_create(user=request.user)

    if request.method == 'POST':
        form = TOTPVerifyForm(request.POST)
        if form.is_valid():
            token = form.cleaned_data['token']
            if device.verify_token(token):
                device.is_confirmed = True
                device.save()
                messages.success(
                    request,
                    "Google Authenticator (TOTP) successfully enabled! "
                    "You can now use Google Authenticator for forgot password recovery."
                )
                return redirect('totp-setup')
            else:
                messages.error(request, "Invalid 6-digit verification code. Please check Google Authenticator and try again.")
    else:
        form = TOTPVerifyForm()

    qr_svg = device.get_qr_code_svg() if not device.is_confirmed else None

    return render(
        request,
        "staff/totp_setup.html",
        {
            "device": device,
            "form": form,
            "qr_svg": qr_svg,
            "secret": device.secret,
        }
    )


@login_required(login_url='login-staff')
def totp_disable(request):
    if request.method == 'POST':
        deleted_count, _ = StaffTOTPDevice.objects.filter(user=request.user).delete()
        if deleted_count:
            messages.success(request, "Google Authenticator (TOTP) has been disabled.")
        return redirect('totp-setup')
    return redirect('totp-setup')


def forgot_password(request):
    if request.user.is_authenticated:
        logout(request)

    if request.method == 'POST':
        form = ForgotPasswordRequestForm(request.POST)
        if form.is_valid():
            identity = form.cleaned_data['identity'].strip()
            user = User.objects.filter(
                Q(username__iexact=identity) | Q(email__iexact=identity)
            ).first()

            if not user:
                messages.error(request, "No account found matching that username or email.")
            elif not hasattr(user, 'totp_device') or not user.totp_device.is_confirmed:
                messages.error(
                    request,
                    "Google Authenticator (TOTP) is not enabled for this account. "
                    "Password recovery is only available when TOTP is enabled in account settings. "
                    "Please contact an administrator to reset your password."
                )
            else:
                request.session['totp_reset_user_id'] = user.id
                request.session['totp_reset_username'] = user.username
                request.session['totp_reset_verified'] = False
                return redirect('forgot-password-verify')
    else:
        form = ForgotPasswordRequestForm()

    return render(request, "staff/forgot_password.html", {"form": form})


def forgot_password_verify(request):
    user_id = request.session.get('totp_reset_user_id')
    if not user_id:
        return redirect('forgot-password')

    user = get_object_or_404(User, id=user_id)
    device = getattr(user, 'totp_device', None)
    if not device or not device.is_confirmed:
        messages.error(request, "Google Authenticator is not enabled for this account.")
        return redirect('forgot-password')

    if request.method == 'POST':
        form = TOTPVerifyForm(request.POST)
        if form.is_valid():
            token = form.cleaned_data['token']
            if device.verify_token(token):
                request.session['totp_reset_verified'] = True
                messages.success(request, "Two-factor verification successful! Please enter your new password.")
                return redirect('forgot-password-reset')
            else:
                messages.error(request, "Invalid or expired 6-digit code. Please check Google Authenticator.")
    else:
        form = TOTPVerifyForm()

    return render(
        request,
        "staff/forgot_password_verify.html",
        {
            "form": form,
            "username": user.username,
        }
    )


def forgot_password_reset(request):
    user_id = request.session.get('totp_reset_user_id')
    is_verified = request.session.get('totp_reset_verified')

    if not user_id or not is_verified:
        return redirect('forgot-password')

    user = get_object_or_404(User, id=user_id)

    if request.method == 'POST':
        form = SetPasswordForm(user, request.POST)
        if form.is_valid():
            form.save()
            request.session.pop('totp_reset_user_id', None)
            request.session.pop('totp_reset_username', None)
            request.session.pop('totp_reset_verified', None)
            messages.success(request, "Your password has been successfully reset! Please log in with your new password.")
            return redirect('login-staff')
        else:
            messages.error(request, "Please correct the password errors below.")
    else:
        form = SetPasswordForm(user)

    return render(
        request,
        "staff/forgot_password_reset.html",
        {
            "form": form,
            "username": user.username,
        }
    )


@login_required(login_url='login-staff')
def profile_view(request):
    user = request.user
    profile_form = ProfileUpdateForm(instance=user)
    password_form = PasswordChangeForm(user=user)
    active_tab = "profile"

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "update_profile":
            profile_form = ProfileUpdateForm(request.POST, instance=user)
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, "Your profile details have been successfully updated.")
                return redirect("profile")
            else:
                messages.error(request, "Please correct the errors in your profile details.")
                active_tab = "profile"

        elif action == "change_password":
            password_form = PasswordChangeForm(user=user, data=request.POST)
            if password_form.is_valid():
                user_obj = password_form.save()
                update_session_auth_hash(request, user_obj)  # Keeps user logged in
                messages.success(request, "Your password was successfully updated.")
                return redirect("profile")
            else:
                messages.error(request, "Please correct the errors in your password change form.")
                active_tab = "password"

    totp_device = getattr(user, "totp_device", None)
    totp_enabled = bool(totp_device and totp_device.is_confirmed)

    return render(
        request,
        "staff/profile.html",
        {
            "profile_form": profile_form,
            "password_form": password_form,
            "active_tab": active_tab,
            "totp_enabled": totp_enabled,
        },
    )


