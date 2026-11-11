import datetime
import re
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.utils import timezone

CODE_RE = re.compile(r"^[A-Z0-9_-]{2,20}$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9\s-]{6,18}$")
MAX_SALARY = Decimal("100000000")
CENTS = Decimal("0.01")


def clean_code(value):
    code = (value or "").strip().upper()
    if not CODE_RE.fullmatch(code):
        raise ValidationError("Use 2 to 20 letters, numbers, dashes or underscores.")
    return code


def clean_phone(value):
    value = (value or "").strip()
    if value and not PHONE_RE.fullmatch(value):
        raise ValidationError("Enter a valid phone number (digits, spaces, dashes, optional + at the start).")
    return value


def clean_salary(value):
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValidationError("Salary must be a number.")
    if not amount.is_finite():
        raise ValidationError("Salary must be a number.")
    if amount <= 0:
        raise ValidationError("Salary must be more than 0.")
    if amount > MAX_SALARY:
        raise ValidationError("Salary is too large.")
    if amount != amount.quantize(CENTS):
        raise ValidationError("Salary can have at most 2 decimal places.")
    return amount


def clean_join_date(value):
    today = timezone.localdate()
    if value < datetime.date(1990, 1, 1):
        raise ValidationError("Join date is too far in the past.")
    if value > today + datetime.timedelta(days=90):
        raise ValidationError("Join date cannot be more than 90 days in the future.")
    return value
