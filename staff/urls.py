from django.urls import path
from staff.views import (
    forgot_password,
    forgot_password_reset,
    forgot_password_verify,
    profile_view,
    registerstaff,
    stafflogin,
    stafflogout,
    totp_disable,
    totp_setup,
    mandatory_password_change_view,
    reset_personnel_account_view,
    provision_single_account_view,
)

urlpatterns = [
    path('profile/', profile_view, name='profile'),
    path('register-staff/', registerstaff, name='register-staff'),
    path('login-staff/', stafflogin, name='login-staff'),
    path('logout-staff/', stafflogout, name='logout-staff'),
    path('two-factor/', totp_setup, name='totp-setup'),
    path('two-factor/disable/', totp_disable, name='totp-disable'),
    path('forgot-password/', forgot_password, name='forgot-password'),
    path('forgot-password/verify/', forgot_password_verify, name='forgot-password-verify'),
    path('forgot-password/reset/', forgot_password_reset, name='forgot-password-reset'),
    path('mandatory-password-change/', mandatory_password_change_view, name='mandatory-password-change'),
    path('account/<str:staff_id>/reset/', reset_personnel_account_view, name='personnel-account-reset'),
    path('account/<str:staff_id>/provision/', provision_single_account_view, name='personnel-account-provision'),
]