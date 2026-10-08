from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, SetPasswordForm
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from staff.forms import (
    ForgotPasswordRequestForm, ProfileUpdateForm, RegisterStaffForm,
    TOTPVerifyForm, MandatoryPasswordChangeForm
)
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
    if request.user.is_authenticated:
        assignment = getattr(request.user, 'assignment', None)
        if assignment and assignment.must_change_password:
            return redirect('mandatory-password-change')
        from gnfs_komenda.security import get_personnel_for_user
        if get_personnel_for_user(request.user):
            return redirect('my-dashboard')
        return redirect('personnel')

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)

            assignment = getattr(user, 'assignment', None)
            if assignment and assignment.must_change_password:
                messages.warning(request, "First login detected. You must change your temporary password before accessing your workspace.")
                return redirect('mandatory-password-change')

            messages.success(request, "Login successful!")
            next_url = request.GET.get('next') or request.POST.get('next')
            if next_url:
                return redirect(next_url)

            from gnfs_komenda.security import get_personnel_for_user
            if get_personnel_for_user(user):
                return redirect('my-dashboard')
            return redirect("personnel")
        else:
            messages.error(request, "Invalid Staff ID / username or password.")
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


@login_required(login_url='login-staff')
def mandatory_password_change_view(request):
    """
    Mandatory password change view for users on their first login or after account reset.
    Enforced by EnforcePasswordChangeMiddleware.
    """
    user = request.user
    assignment = getattr(user, 'assignment', None)

    if not assignment or not assignment.must_change_password:
        from gnfs_komenda.security import get_personnel_for_user
        if get_personnel_for_user(user):
            return redirect('my-dashboard')
        return redirect('personnel')

    if request.method == 'POST':
        form = MandatoryPasswordChangeForm(user, request.POST)
        if form.is_valid():
            form.save()
            assignment.must_change_password = False
            assignment.password_changed_at = timezone.now()
            assignment.save(update_fields=['must_change_password', 'password_changed_at', 'updated_at'])

            # Update session hash so user doesn't get logged out by Django
            update_session_auth_hash(request, user)

            from gnfs_komenda.models import AuditLog
            AuditLog.objects.create(
                user=user,
                action="PASSWORD_CHANGED",
                target_type="User",
                target_id=str(user.id),
                description=f"User {user.username} successfully established a new private password.",
                ip_address=request.META.get('REMOTE_ADDR')
            )

            messages.success(request, "Your private password has been successfully established. Welcome to your GNFS Dashboard!")
            from gnfs_komenda.security import get_personnel_for_user
            if get_personnel_for_user(user):
                return redirect('my-dashboard')
            return redirect('personnel')
        else:
            messages.error(request, "Please correct the errors in the password form below.")
    else:
        form = MandatoryPasswordChangeForm(user)

    return render(request, "staff/mandatory_password_change.html", {"form": form})


@login_required(login_url='login-staff')
def reset_personnel_account_view(request, staff_id):
    """
    Authorized administrators can reset a personnel user's password back to the temporary credential.
    Mandatory password change is set to True, and previous sessions are invalidated.
    Protected system bootstrap accounts cannot be reset.
    """
    from django.core.exceptions import PermissionDenied
    from gnfs_komenda.models import NorminalRoll
    from gnfs_komenda.security import get_user_role
    from staff.services.account_provisioning_service import reset_personnel_account

    user_role = get_user_role(request.user)
    if not (request.user.is_superuser or user_role in ("NATIONAL_ADMIN", "REGIONAL_ADMIN", "DISTRICT_ADMIN")):
        raise PermissionDenied("You do not have authorization to reset personnel account credentials.")

    personnel = get_object_or_404(NorminalRoll, staffID=staff_id)

    if request.method == "POST":
        try:
            reset_personnel_account(personnel, requested_by=request.user)
            messages.success(request, f"Account access for {personnel.fullname} ({personnel.staffID}) has been reset to temporary password. Mandatory password change enforced.")
        except PermissionDenied as e:
            raise e
        except Exception as e:
            messages.error(request, f"Unable to reset account: {str(e)}")

    return redirect('staff-details', staffID=staff_id)


@login_required(login_url='login-staff')
def provision_single_account_view(request, staff_id):
    """
    Explicitly provision a user account for a personnel record if one does not already exist.
    """
    from django.core.exceptions import PermissionDenied
    from gnfs_komenda.models import NorminalRoll
    from gnfs_komenda.security import get_user_role
    from staff.services.account_provisioning_service import provision_personnel_account

    user_role = get_user_role(request.user)
    if not (request.user.is_superuser or user_role in ("NATIONAL_ADMIN", "REGIONAL_ADMIN", "DISTRICT_ADMIN")):
        raise PermissionDenied("You do not have authorization to provision personnel accounts.")

    personnel = get_object_or_404(NorminalRoll, staffID=staff_id)

    if request.method == "POST":
        try:
            user, created, msg = provision_personnel_account(personnel, created_by=request.user)
            if created:
                messages.success(request, f"User account created for {personnel.fullname} (Staff ID: {staff_id}) with temporary password.")
            else:
                messages.info(request, msg)
        except Exception as e:
            messages.error(request, f"Unable to provision account: {str(e)}")

    return redirect('staff-details', staffID=staff_id)


