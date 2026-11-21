import calendar
import datetime
import re
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import User
from accounts.permissions import jwt_login_required, role_required
from employees.models import Employee
from setup.models import Department

from . import reports, services
from .models import Attendance

MIN_DATE = datetime.date(2020, 1, 1)
MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _employee_for(request):
    return Employee.objects.filter(user=request.user).first()


def _page(request, employee, error=None, status=200):
    today = timezone.localdate()
    record = None
    recent = []
    if employee is not None:
        record = Attendance.objects.filter(employee=employee, work_date=today).first()
        recent = Attendance.objects.filter(employee=employee)[:14]
    context = {"employee": employee, "today": today, "record": record, "recent": recent, "error": error}
    return render(request, "attendance/my.html", context, status=status)


@role_required("hr", "employee")
def my_attendance(request):
    return _page(request, _employee_for(request))


@role_required("hr", "employee")
@require_POST
def check_in(request):
    employee = _employee_for(request)
    if employee is None:
        return _page(request, None, status=422)
    try:
        services.check_in(employee)
    except services.AttendanceError as error:
        return _page(request, employee, error=error.message, status=error.status)
    messages.success(request, "Checked in. Have a good day.")
    return redirect("my_attendance")


@role_required("hr", "employee")
@require_POST
def check_out(request):
    employee = _employee_for(request)
    if employee is None:
        return _page(request, None, status=422)
    try:
        services.check_out(employee)
    except services.AttendanceError as error:
        return _page(request, employee, error=error.message, status=error.status)
    messages.success(request, "Checked out. See you tomorrow.")
    return redirect("my_attendance")


def _parse_month(value, today, join_date):
    year, month = today.year, today.month
    match = MONTH_RE.fullmatch(value or "")
    if match:
        y, m = int(match.group(1)), int(match.group(2))
        if 1 <= m <= 12 and y >= MIN_DATE.year:
            year, month = y, m
    if (year, month) > (today.year, today.month):
        year, month = today.year, today.month
    if (year, month) < (join_date.year, join_date.month):
        year, month = join_date.year, join_date.month
    return year, month


def _month_key(day):
    return f"{day.year}-{day.month:02d}"


@role_required("hr", "employee")
def my_calendar(request):
    employee = _employee_for(request)
    if employee is None:
        return render(request, "employees/no_profile.html")
    return redirect("attendance_calendar", pk=employee.pk)


@jwt_login_required
def calendar_view(request, pk):
    employee = get_object_or_404(
        Employee.objects.select_related("user", "department", "designation"), pk=pk
    )
    if request.user.role == User.Role.EMPLOYEE and employee.user_id != request.user.pk:
        raise PermissionDenied

    today = timezone.localdate()
    year, month = _parse_month(request.GET.get("month"), today, employee.join_date)
    weeks, counts = reports.month_calendar(employee, year, month, today)

    first = datetime.date(year, month, 1)
    last = datetime.date(year, month, calendar.monthrange(year, month)[1])
    before = first - datetime.timedelta(days=1)
    after = last + datetime.timedelta(days=1)
    join_key = (employee.join_date.year, employee.join_date.month)
    prev_month = _month_key(before) if (before.year, before.month) >= join_key else None
    next_month = _month_key(after) if (after.year, after.month) <= (today.year, today.month) else None
    summary = [
        (reports.LABELS[s], reports.BADGES[s], counts[s]) for s in reports.SUMMARY_ORDER if counts[s]
    ]
    return render(request, "attendance/calendar.html", {
        "emp": employee,
        "weeks": weeks,
        "weekdays": WEEKDAYS,
        "summary": summary,
        "year": year,
        "month": month,
        "month_label": first.strftime("%B %Y"),
        "prev_month": prev_month,
        "next_month": next_month,
    })


@role_required("admin", "hr")
def board(request):
    today = timezone.localdate()
    day, notice = today, None
    raw = request.GET.get("date", "").strip()
    if raw:
        try:
            requested = datetime.date.fromisoformat(raw)
        except ValueError:
            notice = "That date is not valid. Showing today."
        else:
            if requested > today:
                notice = "Future dates are not available. Showing today."
            elif requested < MIN_DATE:
                notice = "That date is too far back. Showing today."
            else:
                day = requested

    department = request.GET.get("department", "")
    department_id = int(department) if department.isdecimal() else None
    status = request.GET.get("status", "")
    if status not in reports.BOARD_FILTERS:
        status = ""

    rows, counts, holiday_name, is_weekend = reports.board(day, today, department_id)
    shown = [r for r in rows if r["status"] == status] if status else rows
    page = Paginator(shown, 50).get_page(request.GET.get("page"))

    base = {"date": day.isoformat()}
    if department_id:
        base["department"] = department_id
    query = dict(base)
    if status:
        query["status"] = status
    cards = [(k, reports.LABELS[k], reports.BADGES[k], counts[k]) for k in reports.BOARD_FILTERS]

    return render(request, "attendance/board.html", {
        "page": page,
        "day": day,
        "today": today,
        "notice": notice,
        "department": department,
        "status": status,
        "departments": Department.objects.order_by("name"),
        "statuses": [(k, reports.LABELS[k]) for k in reports.BOARD_FILTERS],
        "cards": cards,
        "base_qs": urlencode(base),
        "qs": urlencode(query),
        "holiday_name": holiday_name,
        "is_weekend": is_weekend,
        "total": len(rows),
    })
