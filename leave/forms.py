import datetime

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from setup.models import LeaveType

from . import services


class LeaveForm(forms.Form):
    leave_type = forms.ModelChoiceField(queryset=LeaveType.objects.none(), empty_label="Choose a type")
    start_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    end_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    reason = forms.CharField(
        min_length=5,
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="At least 5 characters.",
    )

    def __init__(self, employee, *args, **kwargs):
        self.employee = employee
        tomorrow = timezone.localdate() + datetime.timedelta(days=1)
        kwargs.setdefault("initial", {"start_date": tomorrow, "end_date": tomorrow})
        super().__init__(*args, **kwargs)
        self.fields["leave_type"].queryset = LeaveType.objects.order_by("name")
        for field in self.fields.values():
            is_select = isinstance(field.widget, forms.Select)
            field.widget.attrs["class"] = "form-select" if is_select else "form-control"

    def clean(self):
        data = super().clean()
        leave_type, start, end = data.get("leave_type"), data.get("start_date"), data.get("end_date")
        if leave_type and start and end:
            try:
                services.validate_request(self.employee, leave_type, start, end)
            except ValidationError as error:
                for field, texts in error.message_dict.items():
                    self.add_error(field, texts)
        return data
