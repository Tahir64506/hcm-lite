import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone

from setup.models import Company

from .models import Attendance


class AttendanceError(Exception):
    status = 409

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class AlreadyCheckedIn(AttendanceError):
    status = 409


class AlreadyCheckedOut(AttendanceError):
    status = 409


class NotCheckedIn(AttendanceError):
    status = 422


class CheckInNotAllowed(AttendanceError):
    status = 422


def check_in(employee, now=None):
    """The server stamps the time. `now` exists only so tests can control the clock."""
    now = now or timezone.now()
    local = timezone.localtime(now)
    day = local.date()
    if day < employee.join_date:
        raise CheckInNotAllowed(f"Your join date is {employee.join_date:%d %b %Y}. You cannot check in before it.")

    company = Company.objects.first()
    deadline = datetime.datetime.combine(day, company.office_start) + datetime.timedelta(
        minutes=company.grace_minutes
    )
    late = local.replace(tzinfo=None) > deadline
    try:
        with transaction.atomic():
            return Attendance.objects.create(employee=employee, work_date=day, check_in=now, is_late=late)
    except IntegrityError:
        raise AlreadyCheckedIn("You have already checked in today.")


def check_out(employee, now=None):
    now = now or timezone.now()
    day = timezone.localtime(now).date()
    company = Company.objects.first()
    with transaction.atomic():
        record = Attendance.objects.select_for_update().filter(employee=employee, work_date=day).first()
        if record is None:
            raise NotCheckedIn("You have not checked in today.")
        if record.check_out is not None:
            raise AlreadyCheckedOut("You have already checked out today.")
        minimum = datetime.timedelta(minutes=int(company.half_day_hours * 60))
        record.check_out = now
        record.is_half_day = (now - record.check_in) < minimum
        record.save(update_fields=["check_out", "is_half_day", "updated_at"])
    return record
