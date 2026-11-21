from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.permissions import role_required
from employees.models import Employee

from . import services
from .correction_forms import CorrectionForm
from .models import Attendance, CorrectionRequest


def _add_errors(form, error):
    if hasattr(error, "error_dict"):
        for field, texts in error.message_dict.items():
            form.add_error(field if field in form.fields else None, texts)
    else:
        form.add_error(None, error.messages)


@role_required("hr", "employee")
def my_corrections(request):
    employee = Employee.objects.filter(user=request.user).first()
    if employee is None:
        return render(request, "employees/no_profile.html")

    form = CorrectionForm(employee, request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            services.create_correction(
                request.user, employee, data["work_date"], data["check_in_time"],
                data["check_out_time"], data["reason"],
            )
        except ValidationError as error:
            _add_errors(form, error)
        else:
            messages.success(request, "Request sent. HR will review it.")
            return redirect("my_corrections")

    items = CorrectionRequest.objects.filter(employee=employee).select_related("decided_by")[:50]
    return render(request, "attendance/corrections.html", {"form": form, "items": items})


@role_required("admin", "hr")
def review(request):
    status = request.GET.get("status", "pending")
    if status not in dict(CorrectionRequest.Status.choices):
        status = ""
    items = CorrectionRequest.objects.select_related("employee__user", "employee__department", "decided_by")
    if status:
        items = items.filter(status=status)
    page = Paginator(items, 20).get_page(request.GET.get("page"))
    rows = list(page)

    existing = {}
    if rows:
        found = Attendance.objects.filter(
            employee_id__in={r.employee_id for r in rows}, work_date__in={r.work_date for r in rows}
        )
        existing = {(a.employee_id, a.work_date): a for a in found}
    for row in rows:
        row.current = existing.get((row.employee_id, row.work_date))

    return render(request, "attendance/review.html", {
        "page": page,
        "rows": rows,
        "status": status,
        "statuses": CorrectionRequest.Status.choices,
        "pending_count": CorrectionRequest.objects.filter(status=CorrectionRequest.Status.PENDING).count(),
        "can_decide": request.user.role == "hr",
    })


@role_required("hr")
@require_POST
def decide(request, pk):
    decision = request.POST.get("decision")
    if decision not in ("approve", "reject"):
        return HttpResponseBadRequest("Unknown decision.")
    get_object_or_404(CorrectionRequest, pk=pk)
    try:
        correction = services.decide(request.user, pk, decision == "approve", request.POST.get("comment", ""))
    except services.CorrectionError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, f"Request {correction.get_status_display().lower()}.")
    return redirect("correction_review")


@role_required("hr", "employee")
@require_POST
def cancel(request, pk):
    correction = get_object_or_404(CorrectionRequest.objects.select_related("employee"), pk=pk)
    if correction.employee.user_id != request.user.pk:
        raise PermissionDenied
    try:
        services.cancel(request.user, pk)
    except services.CorrectionError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, "Request cancelled.")
    return redirect("my_corrections")
