import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from audit.services import model_snapshot, record
from employees.models import Employee
from setup.models import Company, Holiday

from .models import LeaveRequest

MAX_BACKDAYS = 30
MAX_AHEAD_DAYS = 365
MAX_SPAN_DAYS = 60
HOLDING = (LeaveRequest.Status.PENDING, LeaveRequest.Status.APPROVED)


class LeaveError(Exception):
    status = 422

    def __init__(self, message, status=422):
        super().__init__(message)
        self.message = message
        self.status = status


def working_days(start, end):
    """Days between start and end (inclusive) that are not weekends or public holidays."""
    company = Company.objects.first()
    weekend = set(company.weekend_list()) if company else {5, 6}
    holidays = set(Holiday.objects.filter(date__range=(start, end)).values_list("date", flat=True))
    count = 0
    day = start
    while day <= end:
        if day.weekday() not in weekend and day not in holidays:
            count += 1
        day += datetime.timedelta(days=1)
    return count


def used_days(employee, leave_type, year, statuses):
    total = LeaveRequest.objects.filter(
        employee=employee, leave_type=leave_type, start_date__year=year, status__in=statuses
    ).aggregate(total=Sum("days"))["total"]
    return total or 0


def balance_row(employee, leave_type, year):
    approved = used_days(employee, leave_type, year, [LeaveRequest.Status.APPROVED])
    pending = used_days(employee, leave_type, year, [LeaveRequest.Status.PENDING])
    quota = leave_type.yearly_quota
    remaining = None if quota is None else max(quota - approved - pending, 0)
    return {
        "leave_type": leave_type,
        "quota": quota,
        "approved": approved,
        "pending": pending,
        "remaining": remaining,
    }


def balances(employee, year):
    from setup.models import LeaveType

    return [balance_row(employee, t, year) for t in LeaveType.objects.order_by("name")]


def validate_request(employee, leave_type, start, end, now=None):
    """Return the number of working days, or raise ValidationError keyed by form field."""
    now = now or timezone.now()
    today = timezone.localtime(now).date()

    if end < start:
        raise ValidationError({"end_date": "End date cannot be before the start date."})
    if start.year != end.year:
        raise ValidationError({"end_date": "A request must stay within one calendar year. Send two requests instead."})
    if (end - start).days + 1 > MAX_SPAN_DAYS:
        raise ValidationError({"end_date": f"A single request can cover at most {MAX_SPAN_DAYS} days."})
    if start < today - datetime.timedelta(days=MAX_BACKDAYS):
        raise ValidationError({"start_date": f"Leave can start at most {MAX_BACKDAYS} days in the past."})
    if end > today + datetime.timedelta(days=MAX_AHEAD_DAYS):
        raise ValidationError({"end_date": "Leave cannot be requested more than a year ahead."})
    if start < employee.join_date:
        raise ValidationError({"start_date": "This is before the join date."})
    if employee.exit_date and end > employee.exit_date:
        raise ValidationError({"end_date": "This is after the exit date."})

    days = working_days(start, end)
    if days == 0:
        raise ValidationError(
            {"end_date": "There are no working days in this range. Weekends and public holidays are not counted."}
        )

    clash = LeaveRequest.objects.filter(
        employee=employee, status__in=HOLDING, start_date__lte=end, end_date__gte=start
    ).first()
    if clash:
        raise ValidationError({
            "start_date": (
                f"You already have {clash.get_status_display().lower()} leave "
                f"from {clash.start_date:%d %b %Y} to {clash.end_date:%d %b %Y}."
            )
        })

    quota = leave_type.yearly_quota
    if quota is not None:
        used = used_days(employee, leave_type, start.year, HOLDING)
        left = max(quota - used, 0)
        if days > left:
            raise ValidationError({
                "leave_type": (
                    f"Not enough {leave_type.name} balance: {left} day(s) left in {start.year}, "
                    f"you asked for {days}."
                )
            })
    return days


def create_request(actor, employee, leave_type, start, end, reason, now=None):
    reason = (reason or "").strip()
    if len(reason) < 5 or len(reason) > 500:
        raise ValidationError({"reason": "Give a reason in 5 to 500 characters."})
    with transaction.atomic():
        Employee.objects.select_for_update().get(pk=employee.pk)  # one request at a time per employee
        days = validate_request(employee, leave_type, start, end, now)
        leave = LeaveRequest.objects.create(
            employee=employee, leave_type=leave_type, start_date=start, end_date=end,
            days=days, reason=reason,
        )
        record(actor, "create", "LeaveRequest", leave.pk, None, model_snapshot(leave))
    return leave


@transaction.atomic
def decide(actor, leave_id, approve, comment, now=None):
    now = now or timezone.now()
    try:
        leave = LeaveRequest.objects.select_for_update().get(pk=leave_id)
    except LeaveRequest.DoesNotExist:
        raise LeaveError("Request not found.", 404)
    if leave.status != LeaveRequest.Status.PENDING:
        raise LeaveError("This request was already decided.", 409)
    if leave.employee.user_id == actor.pk:
        raise LeaveError("You cannot decide your own request. Ask another HR user.", 403)
    comment = (comment or "").strip()[:500]
    if not approve and not comment:
        raise LeaveError("A comment is required when rejecting.")

    if approve:
        quota = leave.leave_type.yearly_quota
        if quota is not None:
            approved = used_days(
                leave.employee, leave.leave_type, leave.start_date.year, [LeaveRequest.Status.APPROVED]
            )
            if approved + leave.days > quota:
                raise LeaveError(
                    f"Approving would exceed the {leave.leave_type.name} quota "
                    f"({quota} days a year, {approved} already approved)."
                )

    before = model_snapshot(leave)
    leave.status = LeaveRequest.Status.APPROVED if approve else LeaveRequest.Status.REJECTED
    leave.hr_comment = comment
    leave.decided_by = actor
    leave.decided_at = now
    leave.save()
    record(actor, "approve" if approve else "reject", "LeaveRequest", leave.pk, before, model_snapshot(leave))
    return leave


@transaction.atomic
def cancel(actor, leave_id, now=None):
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    try:
        leave = LeaveRequest.objects.select_for_update().get(pk=leave_id)
    except LeaveRequest.DoesNotExist:
        raise LeaveError("Request not found.", 404)
    if leave.employee.user_id != actor.pk:
        raise LeaveError("You can cancel only your own requests.", 403)
    if leave.status == LeaveRequest.Status.APPROVED:
        if leave.start_date <= today:
            raise LeaveError("Leave that has already started cannot be cancelled. Ask HR.")
    elif leave.status != LeaveRequest.Status.PENDING:
        raise LeaveError("Only pending or upcoming approved requests can be cancelled.")

    before = model_snapshot(leave)
    leave.status = LeaveRequest.Status.CANCELLED
    leave.save()
    record(actor, "cancel", "LeaveRequest", leave.pk, before, model_snapshot(leave))
    return leave
