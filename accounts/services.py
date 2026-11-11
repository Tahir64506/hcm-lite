import secrets
import string

from django.db import transaction

from audit.services import model_snapshot, record

from .models import User

_ALPHABET = string.ascii_letters + string.digits


def generate_temp_password():
    """Random temporary password with upper case, lower case, digits and a symbol."""
    while True:
        pwd = "".join(secrets.choice(_ALPHABET) for _ in range(10)) + "#" + str(secrets.randbelow(90) + 10)
        if any(c.islower() for c in pwd) and any(c.isupper() for c in pwd):
            return pwd


@transaction.atomic
def create_hr_user(actor, name, email):
    temp = generate_temp_password()
    user = User.objects.create_user(
        username=email,
        email=email,
        password=temp,
        first_name=name,
        role=User.Role.HR,
        must_change_password=True,
    )
    record(actor, "create", "User", user.pk, None, model_snapshot(user))
    return user, temp


@transaction.atomic
def reset_password(actor, user):
    if user.pk == actor.pk:
        raise ValueError("Use Change password for your own account.")
    before = model_snapshot(user)
    temp = generate_temp_password()
    user.set_password(temp)
    user.must_change_password = True
    user.save()
    record(actor, "reset_password", "User", user.pk, before, model_snapshot(user))
    return temp


@transaction.atomic
def set_active(actor, user, active):
    if not active and user.pk == actor.pk:
        raise ValueError("You cannot deactivate your own account.")
    if user.is_active == active:
        return
    before = model_snapshot(user)
    user.is_active = active
    user.save(update_fields=["is_active"])
    record(actor, "activate" if active else "deactivate", "User", user.pk, before, model_snapshot(user))
