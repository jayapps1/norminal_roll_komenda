from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

User = get_user_model()


class GNFSAuthBackend(ModelBackend):
    """
    Custom authentication backend that supports case-insensitive username/Staff ID lookup
    while preserving standard Django hashed password verification and permissions.
    """
    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(User.USERNAME_FIELD)
        if not username or not password:
            return None

        # 1. Exact match
        user = User.objects.filter(username=username).first()
        if not user:
            # 2. Case-insensitive match (for Staff IDs such as GNFS-101 / gnfs-101)
            user = User.objects.filter(username__iexact=username).first()

        if user and user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
