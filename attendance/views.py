from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.permissions import role_required
from employees.models import Employee

from . import services
from .models import Attendance


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
