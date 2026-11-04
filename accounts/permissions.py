from functools import wraps

from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect


def jwt_login_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("login")
        return view(request, *args, **kwargs)
    return wrapper


def role_required(*roles):
    """Server-side role check. Use on every protected view, e.g. @role_required("admin", "hr")."""
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect("login")
            if request.user.role not in roles:
                raise PermissionDenied  # returns HTTP 403
            return view(request, *args, **kwargs)
        return wrapper
    return decorator
