import datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.services import model_snapshot, record
from setup.models import Company

from .models import Attendance, CorrectionRequest

MAX_BACKDAYS = 30


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


class CorrectionError(AttendanceError):
    status = 422


def _local_dt(day, clock):
    return timezone.make_aware(datetime.datetime.combine(day, clock))


def is_late(company, day, check_in):
    local = timezone.localtime(check_in)
    deadline = datetime.datetime.combine(day, company.office_start) + datetime.timedelta(
        minutes=company.grace_minutes
    )
    return local.replace(tzinfo=None) > deadline


def is_half_day(company, check_in, check_out):
    return (check_out - check_in) < datetime.timedelta(minutes=int(company.half_day_hours * 60))


def check_in(employee, now=None):
    """The server stamps the time. `now` exists only so tests can control the clock."""
    now = now or timezone.now()
    day = timezone.localtime(now).date()
    if day < employee.join_date:
        raise CheckInNotAllowed(f"Your join date is {employee.join_date:%d %b %Y}. You cannot check in before it.")

    company = Company.objects.first()
    late = is_late(company, day, now)
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
        found = Attendance.objects.select_for_update().filter(employee=employee, work_date=day).first()
        if found is None:
            raise NotCheckedIn("You have not checked in today.")
        if found.check_out is not None:
            raise AlreadyCheckedOut("You have already checked out today.")
        found.check_out = now
        found.is_half_day = is_half_day(company, found.check_in, now)
        found.save(update_fields=["check_out", "is_half_day", "updated_at"])
    return found


# ---------- Corrections ----------

def validate_correction(employee, day, check_in_time, check_out_time, now=None):
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    if day > today:
        raise ValidationError({"work_date": "You cannot request a correction for a future date."})
    if day < today - datetime.timedelta(days=MAX_BACKDAYS):
        raise ValidationError({"work_date": f"Corrections are accepted for the last {MAX_BACKDAYS} days only."})
    if day < employee.join_date:
        raise ValidationError({"work_date": "This date is before the join date."})
    if employee.exit_date and day > employee.exit_date:
        raise ValidationError({"work_date": "This date is after the exit date."})

    start = _local_dt(day, check_in_time)
    if start > now:
        raise ValidationError({"check_in_time": "Check-in time cannot be in the future."})
    if check_out_time is not None:
        end = _local_dt(day, check_out_time)
        if end <= start:
            raise ValidationError({"check_out_time": "Check-out must be after check-in."})
        if end > now:
            raise ValidationError({"check_out_time": "Check-out time cannot be in the future."})


def create_correction(actor, employee, day, check_in_time, check_out_time, reason, now=None):
    reason = (reason or "").strip()
    if len(reason) < 10 or len(reason) > 500:
        raise ValidationError({"reason": "Explain what happened in 10 to 500 characters."})
    validate_correction(employee, day, check_in_time, check_out_time, now)

    duplicate = ValidationError({"work_date": "You already have a pending request for this date."})
    pending = CorrectionRequest.Status.PENDING
    if CorrectionRequest.objects.filter(employee=employee, work_date=day, status=pending).exists():
        raise duplicate
    try:
        with transaction.atomic():
            correction = CorrectionRequest.objects.create(
                employee=employee,
                work_date=day,
                check_in_time=check_in_time,
                check_out_time=check_out_time,
                reason=reason,
            )
    except IntegrityError:
        raise duplicate
    record(actor, "create", "CorrectionRequest", correction.pk, None, model_snapshot(correction))
    return correction


def _apply_correction(actor, correction):
    employee = correction.employee
    day = correction.work_date
    if day < employee.join_date or (employee.exit_date and day > employee.exit_date):
        raise CorrectionError("This date is outside the employee's employment dates.")

    company = Company.objects.first()
    start = _local_dt(day, correction.check_in_time)
    end = _local_dt(day, correction.check_out_time) if correction.check_out_time else None
    values = {
        "check_in": start,
        "check_out": end,
        "is_late": is_late(company, day, start),
        "is_half_day": end is not None and is_half_day(company, start, end),
        "source": Attendance.Source.CORRECTION,
    }
    existing = Attendance.objects.select_for_update().filter(employee=employee, work_date=day).first()
    if existing is None:
        attendance = Attendance.objects.create(employee=employee, work_date=day, **values)
        record(actor, "create", "Attendance", attendance.pk, None, model_snapshot(attendance))
    else:
        before = model_snapshot(existing)
        for field, value in values.items():
            setattr(existing, field, value)
        existing.save()
        record(actor, "update", "Attendance", existing.pk, before, model_snapshot(existing))


@transaction.atomic
def decide(actor, correction_id, approve, comment, now=None):
    now = now or timezone.now()
    try:
        correction = CorrectionRequest.objects.select_for_update().get(pk=correction_id)
    except CorrectionRequest.DoesNotExist:
        raise CorrectionError("Request not found.")
    if correction.status != CorrectionRequest.Status.PENDING:
        raise CorrectionError("This request was already decided.")
    if correction.employee.user_id == actor.pk:
        raise CorrectionError("You cannot decide your own request. Ask another HR user.")
    comment = (comment or "").strip()[:500]
    if not approve and not comment:
        raise CorrectionError("A comment is required when rejecting.")

    before = model_snapshot(correction)
    if approve:
        _apply_correction(actor, correction)
    correction.status = CorrectionRequest.Status.APPROVED if approve else CorrectionRequest.Status.REJECTED
    correction.hr_comment = comment
    correction.decided_by = actor
    correction.decided_at = now
    correction.save()
    record(actor, "approve" if approve else "reject", "CorrectionRequest", correction.pk,
           before, model_snapshot(correction))
    return correction


@transaction.atomic
def cancel(actor, correction_id):
    try:
        correction = CorrectionRequest.objects.select_for_update().get(pk=correction_id)
    except CorrectionRequest.DoesNotExist:
        raise CorrectionError("Request not found.")
    if correction.employee.user_id != actor.pk:
        raise CorrectionError("You can cancel only your own requests.")
    if correction.status != CorrectionRequest.Status.PENDING:
        raise CorrectionError("Only pending requests can be cancelled.")
    before = model_snapshot(correction)
    correction.status = CorrectionRequest.Status.CANCELLED
    correction.save()
    record(actor, "cancel", "CorrectionRequest", correction.pk, before, model_snapshot(correction))
    return correction
