from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.permissions import role_required
from employees.models import Employee

from . import services
from .forms import LeaveForm
from .models import LeaveRequest

Status = LeaveRequest.Status


def _add_errors(form, error):
    if hasattr(error, "error_dict"):
        for field, texts in error.message_dict.items():
            form.add_error(field if field in form.fields else None, texts)
    else:
        form.add_error(None, error.messages)


@role_required("hr", "employee")
def my_leave(request):
    employee = Employee.objects.filter(user=request.user).first()
    if employee is None:
        return render(request, "employees/no_profile.html")

    form = LeaveForm(employee, request.POST or None)
    failed = False
    if request.method == "POST":
        if form.is_valid():
            data = form.cleaned_data
            try:
                services.create_request(
                    request.user, employee, data["leave_type"], data["start_date"],
                    data["end_date"], data["reason"],
                )
            except ValidationError as error:
                _add_errors(form, error)
                failed = True
            else:
                messages.success(request, "Leave request sent. HR will review it.")
                return redirect("my_leave")
        else:
            failed = True

    today = timezone.localdate()
    items = list(
        LeaveRequest.objects.filter(employee=employee).select_related("leave_type", "decided_by")[:50]
    )
    for item in items:
        item.can_cancel = item.status == Status.PENDING or (
            item.status == Status.APPROVED and item.start_date > today
        )
    return render(request, "leave/my.html", {
        "form": form,
        "items": items,
        "balances": services.balances(employee, today.year),
        "year": today.year,
    }, status=422 if failed else 200)


@role_required("hr", "employee")
@require_POST
def cancel(request, pk):
    leave = get_object_or_404(LeaveRequest.objects.select_related("employee"), pk=pk)
    if leave.employee.user_id != request.user.pk:
        raise PermissionDenied
    try:
        services.cancel(request.user, pk)
    except services.LeaveError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, "Request cancelled.")
    return redirect("my_leave")


@role_required("admin", "hr")
def review(request):
    status = request.GET.get("status", "pending")
    if status not in dict(Status.choices):
        status = ""
    items = LeaveRequest.objects.select_related("employee__user", "leave_type", "decided_by")
    if status:
        items = items.filter(status=status)
    page = Paginator(items, 20).get_page(request.GET.get("page"))
    rows = list(page)
    for row in rows:
        row.balance = services.balance_row(row.employee, row.leave_type, row.start_date.year)
    return render(request, "leave/review.html", {
        "page": page,
        "rows": rows,
        "status": status,
        "statuses": Status.choices,
        "pending_count": LeaveRequest.objects.filter(status=Status.PENDING).count(),
        "can_decide": request.user.role == "hr",
    })


@role_required("hr")
@require_POST
def decide(request, pk):
    decision = request.POST.get("decision")
    if decision not in ("approve", "reject"):
        return HttpResponseBadRequest("Unknown decision.")
    get_object_or_404(LeaveRequest, pk=pk)
    try:
        leave = services.decide(request.user, pk, decision == "approve", request.POST.get("comment", ""))
    except services.LeaveError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, f"Request {leave.get_status_display().lower()}.")
    return redirect("leave_review")
