from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import ProtectedError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.permissions import role_required
from audit.services import model_snapshot, record

from .forms import (
    CompanySettingsForm, DepartmentForm, DesignationForm, FirstSetupForm,
    HolidayForm, LeaveTypeForm,
)
from .models import Company, Department, Designation, Holiday, LeaveType
from .services import is_setup_done, run_first_setup

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

CATALOGS = {
    "departments": {
        "title": "Departments", "singular": "Department",
        "model": Department, "form": DepartmentForm, "order": "name",
        "columns": [
            ("Name", lambda o: o.name),
            ("Status", lambda o: "Active" if o.is_active else "Inactive"),
        ],
    },
    "designations": {
        "title": "Designations", "singular": "Designation",
        "model": Designation, "form": DesignationForm, "order": "name",
        "columns": [("Name", lambda o: o.name)],
    },
    "leave-types": {
        "title": "Leave types", "singular": "Leave type",
        "model": LeaveType, "form": LeaveTypeForm, "order": "name",
        "columns": [
            ("Name", lambda o: o.name),
            ("Code", lambda o: o.code),
            ("Yearly quota", lambda o: o.yearly_quota if o.yearly_quota is not None else "Unlimited"),
            ("Paid", lambda o: "Yes" if o.is_paid else "No"),
        ],
    },
    "holidays": {
        "title": "Public holidays", "singular": "Holiday",
        "model": Holiday, "form": HolidayForm, "order": "date",
        "columns": [
            ("Date", lambda o: o.date.isoformat()),
            ("Name", lambda o: o.name),
        ],
    },
}


def first_setup(request):
    if is_setup_done():
        return redirect("home")

    form = FirstSetupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        run_first_setup(
            company_name=form.cleaned_data["company_name"],
            admin_name=form.cleaned_data["admin_name"],
            email=form.cleaned_data["email"],
            password=form.cleaned_data["password"],
        )
        return redirect("home")
    return render(request, "setup/first_setup.html", {"form": form})


@role_required("admin", "hr")
def company_settings(request):
    company = Company.objects.first()
    if company is None:
        raise Http404
    is_admin = request.user.role == "admin"
    form = None

    if is_admin:
        form = CompanySettingsForm(request.POST or None, instance=company)
        if request.method == "POST":
            before = model_snapshot(company)
            if form.is_valid():
                company = form.save()
                record(request.user, "update", "Company", company.pk, before, model_snapshot(company))
                messages.success(request, "Company settings saved.")
                return redirect("company_settings")
    elif request.method == "POST":
        raise PermissionDenied

    has_location = company.office_lat is not None and company.office_lng is not None
    summary = [
        ("Company name", company.name),
        ("Office start", company.office_start.strftime("%H:%M")),
        ("Grace time", f"{company.grace_minutes} minutes"),
        ("Half day if worked under", f"{company.half_day_hours} hours"),
        ("Weekend", ", ".join(DAY_NAMES[d] for d in company.weekend_list()) or "None"),
        ("Office location", f"{company.office_lat}, {company.office_lng}" if has_location else "Not set"),
        ("Office radius", f"{company.office_radius_m} m"),
    ]
    return render(request, "setup/settings.html", {"form": form, "is_admin": is_admin, "summary": summary})


@role_required("admin", "hr")
def catalog(request, key):
    config = CATALOGS.get(key)
    if config is None:
        raise Http404
    is_admin = request.user.role == "admin"
    form = config["form"](request.POST or None) if is_admin else None

    if request.method == "POST":
        if not is_admin:
            raise PermissionDenied
        if form.is_valid():
            obj = form.save()
            record(request.user, "create", config["model"].__name__, obj.pk, None, model_snapshot(obj))
            messages.success(request, f"{config['singular']} added.")
            return redirect("catalog", key=key)

    objects = config["model"].objects.order_by(config["order"])
    rows = [(obj, [fn(obj) for _, fn in config["columns"]]) for obj in objects]
    headers = [label for label, _ in config["columns"]]
    return render(request, "setup/catalog.html", {
        "key": key,
        "title": config["title"],
        "headers": headers,
        "rows": rows,
        "form": form,
        "is_admin": is_admin,
        "colspan": len(headers) + (1 if is_admin else 0),
    })


@role_required("admin")
@require_POST
def catalog_delete(request, key, pk):
    config = CATALOGS.get(key)
    if config is None:
        raise Http404
    obj = get_object_or_404(config["model"], pk=pk)
    before = model_snapshot(obj)
    try:
        obj.delete()
    except ProtectedError:
        messages.error(request, "This item is in use and cannot be deleted.")
        return redirect("catalog", key=key)
    record(request.user, "delete", config["model"].__name__, pk, before, None)
    messages.success(request, f"{config['singular']} deleted.")
    return redirect("catalog", key=key)
