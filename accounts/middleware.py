from django.contrib.auth.models import AnonymousUser
from django.shortcuts import redirect

from .jwt_utils import get_user_from_token

COOKIE_NAME = "access_token"
SKIP_PREFIXES = ("/admin/", "/static/")  # Django's admin panel uses its own session login
ALLOWED_WHEN_PASSWORD_CHANGE_PENDING = ("/change-password/", "/logout/")


class JWTAuthMiddleware:
    """Reads the JWT from an HttpOnly cookie and sets request.user on every request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if not path.startswith(SKIP_PREFIXES):
            token = request.COOKIES.get(COOKIE_NAME)
            user = get_user_from_token(token) if token else None
            request.user = user or AnonymousUser()
            if user and user.must_change_password and path not in ALLOWED_WHEN_PASSWORD_CHANGE_PENDING:
                return redirect("change_password")
        return self.get_response(request)
