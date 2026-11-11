import csv
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from accounts.models import User
from accounts.permissions import jwt_login_required, role_required
from audit.models import AuditLog
from audit.services import record
from setup.models import Department, Designation

from . import importer, services
from .forms import ContactForm, EmployeeForm, ImportForm
from .models import Employee

EXPORT_COLUMNS = [
    "employee_code", "name", "email", "department", "designation", "join_date",
    "basic_salary", "gender", "phone", "address", "status", "exit_date",
]


@role_required("admin", "hr")
def directory(request):
    employees = Employee.objects.select_related("user", "department", "designation")
    q = request.GET.get("q", "").strip()
    department = request.GET.get("department", "")
    designation = request.GET.get("designation", "")
    status = request.GET.get("status", "")

    if q:
        employees = employees.filter(
            Q(user__first_name__icontains=q)
            | Q(user__email__icontains=q)
            | Q(employee_code__icontains=q)
        )
    if department.isdecimal():
        employees = employees.filter(department_id=int(department))
    if designation.isdecimal():
        employees = employees.filter(designation_id=int(designation))
    if status in dict(Employee.Status.choices):
        employees = employees.filter(status=status)

    page = Paginator(employees.order_by("employee_code"), 20).get_page(request.GET.get("page"))
    filters = {k: v for k, v in (("q", q), ("department", department),
                                 ("designation", designation), ("status", status)) if v}
    return render(request, "employees/list.html", {
        "page": page,
        "q": q,
        "department": department,
        "designation": designation,
        "status": status,
        "departments": Department.objects.order_by("name"),
        "designations": Designation.objects.order_by("name"),
        "statuses": Employee.Status.choices,
        "qs": urlencode(filters),
    })


@role_required("hr")
def employee_new(request):
    form = EmployeeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            employee, temp = services.register_employee(
                request.user, form.cleaned_data, form.cleaned_data.get("existing_user")
            )
        except IntegrityError:
            form.add_error(None, "Could not save: the code or email was just taken. Try again.")
        else:
            return render(request, "employees/created.html", {"employee": employee, "temp_password": temp})
    return render(request, "employees/form.html", {"form": form, "is_edit": False, "employee": None})


@role_required("hr")
def employee_edit(request, pk):
    employee = get_object_or_404(Employee.objects.select_related("user"), pk=pk)
    form = EmployeeForm(request.POST or None, instance=employee)
    if request.method == "POST" and form.is_valid():
        try:
            services.update_employee(request.user, employee, form.cleaned_data)
        except IntegrityError:
            form.add_error(None, "Could not save: the employee code was just taken.")
        else:
            messages.success(request, "Employee updated.")
            return redirect("employee_detail", pk=employee.pk)
    return render(request, "employees/form.html", {"form": form, "is_edit": True, "employee": employee})


@jwt_login_required
def employee_detail(request, pk):
    employee = get_object_or_404(
        Employee.objects.select_related("user", "department", "designation"), pk=pk
    )
    user = request.user
    is_self = employee.user_id == user.pk
    if user.role == User.Role.EMPLOYEE and not is_self:
        raise PermissionDenied

    contact_form = None
    if is_self:
        contact_form = ContactForm(
            request.POST or None, initial={"phone": employee.phone, "address": employee.address}
        )
        if request.method == "POST" and contact_form.is_valid():
            services.update_own_contact(
                user, employee, contact_form.cleaned_data["phone"], contact_form.cleaned_data["address"]
            )
            messages.success(request, "Your contact details were saved.")
            return redirect("employee_detail", pk=employee.pk)
    elif request.method == "POST":
        raise PermissionDenied

    history = None
    if user.role == User.Role.ADMIN:
        history = AuditLog.objects.filter(entity="Employee", entity_id=str(employee.pk))[:10]
    return render(request, "employees/detail.html", {
        "emp": employee,
        "is_self": is_self,
        "contact_form": contact_form,
        "history": history,
    })


@jwt_login_required
def my_profile(request):
    employee = Employee.objects.filter(user=request.user).first()
    if employee is None:
        return render(request, "employees/no_profile.html")
    return redirect("employee_detail", pk=employee.pk)


@role_required("hr")
def export_csv(request):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="employees.csv"'
    writer = csv.writer(response)
    writer.writerow(EXPORT_COLUMNS)
    count = 0
    employees = Employee.objects.select_related("user", "department", "designation").order_by("employee_code")
    for e in employees:
        writer.writerow([
            services.csv_safe(e.employee_code),
            services.csv_safe(e.name),
            services.csv_safe(e.user.email),
            services.csv_safe(e.department.name),
            services.csv_safe(e.designation.name),
            e.join_date.isoformat(),
            e.basic_salary,
            e.gender,
            e.phone,  # validated: digits, spaces, dashes and an optional leading +
            services.csv_safe(e.address),
            e.status,
            e.exit_date.isoformat() if e.exit_date else "",
        ])
        count += 1
    record(request.user, "export", "Employee", "", None, {"rows": count})
    return response


@role_required("hr")
def import_csv(request):
    form = ImportForm(request.POST or None, request.FILES or None)
    context = {"form": form}
    if request.method == "POST" and form.is_valid():
        result = importer.check_csv(form.cleaned_data["file"].read())
        dry_run = form.cleaned_data["dry_run"]
        context.update({
            "result": result,
            "dry_run": dry_run,
            "errors": result.errors[:200],
            "error_count": len(result.errors),
        })
        if not result.fatal and not result.errors and not dry_run:
            try:
                context["logins"] = importer.import_rows(request.user, result.rows)
            except IntegrityError:
                messages.error(request, "Nothing was imported: data changed while importing. Please try again.")
    return render(request, "employees/import.html", context)
