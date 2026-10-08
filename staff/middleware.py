from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse


class EnforcePasswordChangeMiddleware:
    """
    Middleware that intercepts authenticated requests for users whose accounts
    have must_change_password=True, redirecting them strictly to the mandatory
    password change screen until they set a new private password.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            try:
                assignment = getattr(request.user, 'assignment', None)
            except Exception:
                assignment = None

            if assignment and assignment.must_change_password:
                try:
                    change_url = reverse('mandatory-password-change')
                    logout_url = reverse('logout-staff')
                except Exception:
                    change_url = '/staff/mandatory-password-change/'
                    logout_url = '/staff/logout-staff/'

                path = request.path

                # Allow password change view, logout view, and static/media files
                static_url = getattr(settings, 'STATIC_URL', None)
                media_url = getattr(settings, 'MEDIA_URL', None)

                is_allowed = (
                    path.startswith(change_url) or
                    path.startswith(logout_url) or
                    path.startswith('/staff/logout') or
                    path.startswith('/logout') or
                    bool(static_url and static_url != '/' and path.startswith(static_url)) or
                    bool(media_url and media_url != '/' and path.startswith(media_url))
                )

                if not is_allowed:
                    return redirect('mandatory-password-change')

        response = self.get_response(request)
        return response
