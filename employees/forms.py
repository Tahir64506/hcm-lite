from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from accounts.models import User
from setup.models import Department, Designation

from . import validators
from .models import Employee
from .services import next_employee_code

MAX_IMPORT_BYTES = 1_000_000


def style(form):
    for field in form.fields.values():
        widget = field.widget
        if isinstance(widget, forms.CheckboxInput):
            widget.attrs["class"] = "form-check-input"
        elif isinstance(widget, forms.Select):
            widget.attrs["class"] = "form-select"
        else:
            widget.attrs["class"] = "form-control"


class EmployeeForm(forms.Form):
    existing_user = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        label="Link an existing HR login (optional)",
        empty_label="Create a new employee login",
        help_text="If you choose one, its name and email are used and no new login is created.",
    )
    name = forms.CharField(max_length=100, required=False, label="Full name")
    email = forms.EmailField(required=False)
    employee_code = forms.CharField(max_length=20)
    gender = forms.ChoiceField(choices=[("", "Not stated")] + list(Employee.Gender.choices), required=False)
    phone = forms.CharField(max_length=20, required=False)
    address = forms.CharField(max_length=500, required=False, widget=forms.Textarea(attrs={"rows": 2}))
    department = forms.ModelChoiceField(queryset=Department.objects.none())
    designation = forms.ModelChoiceField(queryset=Designation.objects.none())
    join_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    status = forms.ChoiceField(choices=Employee.Status.choices)
    exit_date = forms.DateField(
        required=False, label="Exit date",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    basic_salary = forms.DecimalField(max_digits=12, decimal_places=2, label="Basic salary (monthly)")

    def __init__(self, *args, instance=None, **kwargs):
        self.instance = instance
        if instance is not None:
            kwargs.setdefault("initial", {
                "name": instance.user.first_name,
                "email": instance.user.email,
                "employee_code": instance.employee_code,
                "gender": instance.gender,
                "phone": instance.phone,
                "address": instance.address,
                "department": instance.department_id,
                "designation": instance.designation_id,
                "join_date": instance.join_date,
                "status": instance.status,
                "exit_date": instance.exit_date,
                "basic_salary": instance.basic_salary,
            })
        else:
            kwargs.setdefault("initial", {
                "employee_code": next_employee_code(),
                "status": Employee.Status.PROBATION,
                "join_date": timezone.localdate(),
            })
        super().__init__(*args, **kwargs)

        departments = Department.objects.filter(is_active=True)
        if instance is not None:
            departments = Department.objects.filter(Q(is_active=True) | Q(pk=instance.department_id))
        self.fields["department"].queryset = departments.order_by("name")
        self.fields["designation"].queryset = Designation.objects.order_by("name")

        if instance is None:
            self.fields["existing_user"].queryset = User.objects.filter(
                role=User.Role.HR, is_active=True, employee__isnull=True
            ).order_by("email")
            self.fields["status"].choices = [
                c for c in Employee.Status.choices if c[0] != Employee.Status.EXITED
            ]
        else:
            del self.fields["existing_user"]
            self.fields["email"].disabled = True
        style(self)

    def clean_name(self):
        return self.cleaned_data["name"].strip()

    def clean_email(self):
        if self.instance is not None:
            return self.instance.user.email
        email = self.cleaned_data.get("email", "").strip().lower()
        if email and (
            User.objects.filter(email__iexact=email).exists()
            or User.objects.filter(username__iexact=email).exists()
        ):
            raise ValidationError("This email is already used.")
        return email

    def clean_employee_code(self):
        code = validators.clean_code(self.cleaned_data["employee_code"])
        taken = Employee.objects.filter(employee_code=code)
        if self.instance is not None:
            taken = taken.exclude(pk=self.instance.pk)
        if taken.exists():
            raise ValidationError("This employee code is already used.")
        return code

    def clean_phone(self):
        return validators.clean_phone(self.cleaned_data["phone"])

    def clean_join_date(self):
        return validators.clean_join_date(self.cleaned_data["join_date"])

    def clean_basic_salary(self):
        return validators.clean_salary(self.cleaned_data["basic_salary"])

    def clean(self):
        data = super().clean()
        existing = data.get("existing_user")
        if (self.instance is not None or existing is None) and not data.get("name"):
            self.add_error("name", "This field is required.")
        if self.instance is None and existing is None and not data.get("email"):
            self.add_error("email", "This field is required.")

        status = data.get("status")
        exit_date = data.get("exit_date")
        joined = data.get("join_date")
        if status == Employee.Status.EXITED and not exit_date:
            self.add_error("exit_date", "Required when the status is Exited.")
        if status and status != Employee.Status.EXITED and exit_date:
            self.add_error("exit_date", "Leave empty unless the status is Exited.")
        if exit_date and joined and exit_date < joined:
            self.add_error("exit_date", "Exit date cannot be before the join date.")
        if (
            self.instance is not None
            and status == Employee.Status.EXITED
            and self.instance.user.role != User.Role.EMPLOYEE
        ):
            self.add_error("status", "Exit for HR accounts is handled by Admin (Users page).")
        return data


class ContactForm(forms.Form):
    phone = forms.CharField(max_length=20, required=False)
    address = forms.CharField(max_length=500, required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style(self)

    def clean_phone(self):
        return validators.clean_phone(self.cleaned_data["phone"])


class ImportForm(forms.Form):
    file = forms.FileField(label="CSV file")
    dry_run = forms.BooleanField(
        required=False, initial=True, label="Dry run (check the file without importing anything)"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style(self)

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if not upload.name.lower().endswith(".csv"):
            raise ValidationError("Upload a .csv file.")
        if upload.size > MAX_IMPORT_BYTES:
            raise ValidationError("The file is too large (maximum 1 MB).")
        return upload
