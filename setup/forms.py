import re

from django import forms
from django.contrib.auth.password_validation import validate_password

from accounts.models import User

from .models import Company, Department, Designation, Holiday, LeaveType

WEEKDAYS = [
    ("0", "Monday"), ("1", "Tuesday"), ("2", "Wednesday"), ("3", "Thursday"),
    ("4", "Friday"), ("5", "Saturday"), ("6", "Sunday"),
]


def style_fields(form):
    for field in form.fields.values():
        if isinstance(field.widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
            field.widget.attrs["class"] = "form-check-input"
        else:
            field.widget.attrs["class"] = "form-control"


class FirstSetupForm(forms.Form):
    company_name = forms.CharField(max_length=200)
    admin_name = forms.CharField(max_length=100, label="Your name")
    email = forms.EmailField(label="Admin email")
    password = forms.CharField(widget=forms.PasswordInput)
    confirm_password = forms.CharField(widget=forms.PasswordInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style_fields(self)

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("This email is already used.")
        return email

    def clean_password(self):
        password = self.cleaned_data["password"]
        validate_password(password)
        return password

    def clean(self):
        data = super().clean()
        if data.get("password") and data.get("password") != data.get("confirm_password"):
            self.add_error("confirm_password", "Passwords do not match.")
        return data


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style_fields(self)


class NamedForm(StyledModelForm):
    """Base for models with a unique name (case-insensitive)."""

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        found = self._meta.model.objects.filter(name__iexact=name)
        if self.instance.pk:
            found = found.exclude(pk=self.instance.pk)
        if found.exists():
            raise forms.ValidationError("This name already exists.")
        return name


class CompanySettingsForm(StyledModelForm):
    weekend = forms.MultipleChoiceField(
        choices=WEEKDAYS, widget=forms.CheckboxSelectMultiple, label="Weekend days"
    )
    field_order = [
        "name", "office_start", "grace_minutes", "half_day_hours", "weekend",
        "office_lat", "office_lng", "office_radius_m",
    ]

    class Meta:
        model = Company
        fields = [
            "name", "office_start", "grace_minutes", "half_day_hours",
            "office_lat", "office_lng", "office_radius_m",
        ]
        widgets = {"office_start": forms.TimeInput(attrs={"type": "time"}, format="%H:%M")}
        labels = {
            "name": "Company name",
            "office_start": "Office start time",
            "grace_minutes": "Grace time (minutes)",
            "half_day_hours": "Half day if worked under (hours)",
            "office_lat": "Office latitude",
            "office_lng": "Office longitude",
            "office_radius_m": "Office radius (metres)",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["weekend"].initial = [str(d) for d in self.instance.weekend_list()]

    def clean_weekend(self):
        days = self.cleaned_data["weekend"]
        if len(days) >= 7:
            raise forms.ValidationError("At least one working day is needed.")
        return days

    def clean(self):
        data = super().clean()
        lat, lng = data.get("office_lat"), data.get("office_lng")
        if (lat is None) != (lng is None):
            raise forms.ValidationError("Enter both latitude and longitude, or leave both empty.")
        if lat is not None and not -90 <= lat <= 90:
            self.add_error("office_lat", "Latitude must be between -90 and 90.")
        if lng is not None and not -180 <= lng <= 180:
            self.add_error("office_lng", "Longitude must be between -180 and 180.")
        radius = data.get("office_radius_m")
        if radius is not None and not 10 <= radius <= 5000:
            self.add_error("office_radius_m", "Radius must be between 10 and 5000 metres.")
        half = data.get("half_day_hours")
        if half is not None and not 0 < half <= 12:
            self.add_error("half_day_hours", "Must be more than 0 and at most 12 hours.")
        grace = data.get("grace_minutes")
        if grace is not None and grace > 120:
            self.add_error("grace_minutes", "Grace time cannot be more than 120 minutes.")
        return data

    def save(self, commit=True):
        company = super().save(commit=False)
        company.weekend_days = ",".join(sorted(self.cleaned_data["weekend"]))
        if commit:
            company.save()
        return company


class DepartmentForm(NamedForm):
    class Meta:
        model = Department
        fields = ["name"]


class DesignationForm(NamedForm):
    class Meta:
        model = Designation
        fields = ["name"]


class LeaveTypeForm(NamedForm):
    class Meta:
        model = LeaveType
        fields = ["name", "code", "yearly_quota", "is_paid"]
        labels = {
            "code": "Code (for example annual)",
            "yearly_quota": "Yearly quota in days (empty = unlimited)",
            "is_paid": "Paid leave",
        }

    def clean_code(self):
        code = self.cleaned_data["code"].strip().lower()
        if not re.fullmatch(r"[a-z0-9_]+", code):
            raise forms.ValidationError("Use only lowercase letters, numbers and underscores.")
        return code

    def clean_yearly_quota(self):
        quota = self.cleaned_data["yearly_quota"]
        if quota is not None and quota > 365:
            raise forms.ValidationError("Quota cannot be more than 365 days.")
        return quota


class HolidayForm(StyledModelForm):
    class Meta:
        model = Holiday
        fields = ["date", "name"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}
