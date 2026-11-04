import datetime

import jwt
from django.conf import settings

from .models import User

ALGORITHM = "HS256"


def create_token(user):
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "sub": str(user.pk),
        "role": user.role,
        "iat": now,
        "exp": now + datetime.timedelta(hours=settings.JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def get_user_from_token(token):
    """Return the active user for a valid token, otherwise None."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        return User.objects.get(pk=int(payload["sub"]), is_active=True)
    except (jwt.PyJWTError, User.DoesNotExist, KeyError, ValueError):
        return None
