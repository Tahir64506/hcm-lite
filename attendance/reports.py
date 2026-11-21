import calendar
import datetime
from collections import Counter

from employees.models import Employee
from leave.models import LeaveRequest
from setup.models import Company, Holiday

from .models import Attendance

LABELS = {
    "present": "Present",
    "late": "Late",
    "half_day": "Half day",
    "missing_checkout": "Missing check-out",
    "absent": "Absent",
    "on_leave": "On leave",
    "pending": "Not in yet",
    "weekend": "Weekend",
    "holiday": "Holiday",
    "future": "",
    "before_join": "",
    "after_exit": "",
}
BADGES = {
    "present": "text-bg-success",
    "late": "text-bg-warning",
    "half_day": "text-bg-info",
    "missing_checkout": "text-bg-danger",
    "absent": "text-bg-danger",
    "on_leave": "text-bg-primary",
    "pending": "text-bg-light border",
    "weekend": "text-bg-light border",
    "holiday": "text-bg-secondary",
    "future": "",
    "before_join": "",
    "after_exit": "",
}
BOARD_FILTERS = ["present", "late", "half_day", "missing_checkout", "absent", "on_leave", "pending"]
SUMMARY_ORDER = ["present", "late", "half_day", "missing_checkout", "absent", "on_leave", "holiday", "weekend"]


def approved_leave_days(employee_ids, start, end):
    """Employee id -> set of dates covered by APPROVED leave inside [start, end]."""
    found = {}
    ids = list(employee_ids)
    if not ids:
        return found
    requests = LeaveRequest.objects.filter(
        employee_id__in=ids,
        status=LeaveRequest.Status.APPROVED,
        start_date__lte=end,
        end_date__gte=start,
    )
    for item in requests:
        day = max(item.start_date, start)
        last = min(item.end_date, end)
        dates = found.setdefault(item.employee_id, set())
        while day <= last:
            dates.add(day)
            day += datetime.timedelta(days=1)
    return found


def _settings_for(start, end):
    company = Company.objects.first()
    weekend = set(company.weekend_list()) if company else {5, 6}
    holidays = {h.date: h.name for h in Holiday.objects.filter(date__range=(start, end))}
    return weekend, holidays


def day_status(employee, day, record, today, weekend, holidays, leave_days=()):
    if day < employee.join_date:
        return "before_join"
    if employee.exit_date and day > employee.exit_date:
        return "after_exit"
    if record is not None:
        return record.status_on(today)
    if day.weekday() in weekend:
        return "weekend"
    if day in holidays:
        return "holiday"
    if day in leave_days:
        return "on_leave"
    if day > today:
        return "future"
    if day == today:
        return "pending"
    return "absent"


def month_calendar(employee, year, month, today):
    first = datetime.date(year, month, 1)
    last = datetime.date(year, month, calendar.monthrange(year, month)[1])
    weekend, holidays = _settings_for(first, last)
    records = {
        r.work_date: r
        for r in Attendance.objects.filter(employee=employee, work_date__range=(first, last))
    }
    leave = approved_leave_days([employee.pk], first, last).get(employee.pk, set())

    weeks = []
    counts = Counter()
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
        row = []
        for day in week:
            if day.month != month:
                row.append(None)
                continue
            record = records.get(day)
            status = day_status(employee, day, record, today, weekend, holidays, leave)
            counts[status] += 1
            row.append({
                "date": day,
                "status": status,
                "label": LABELS[status],
                "badge": BADGES[status],
                "record": record,
                "holiday": holidays.get(day),
                "is_today": day == today,
            })
        weeks.append(row)
    return weeks, counts


def board(day, today, department_id=None):
    """Everyone employed on `day`, with their status. Returns (rows, counts, holiday name, is weekend)."""
    employees = (
        Employee.objects.select_related("user", "department", "designation")
        .filter(join_date__lte=day)
        .exclude(exit_date__lt=day)
    )
    if department_id:
        employees = employees.filter(department_id=department_id)
    employees = list(employees.order_by("employee_code"))
    ids = [e.pk for e in employees]

    weekend, holidays = _settings_for(day, day)
    records = {r.employee_id: r for r in Attendance.objects.filter(work_date=day, employee_id__in=ids)}
    leave = approved_leave_days(ids, day, day)

    rows = []
    counts = Counter()
    for employee in employees:
        record = records.get(employee.pk)
        status = day_status(employee, day, record, today, weekend, holidays, leave.get(employee.pk, ()))
        counts[status] += 1
        rows.append({
            "employee": employee,
            "record": record,
            "status": status,
            "label": LABELS[status],
            "badge": BADGES[status],
        })
    return rows, counts, holidays.get(day), day.weekday() in weekend
