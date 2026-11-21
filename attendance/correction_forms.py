import datetime

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from . import services


class CorrectionForm(forms.Form):
    work_date = forms.DateField(
        label="Date", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    )
    check_in_time = forms.TimeField(
        label="Correct check-in time", widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M")
    )
    check_out_time = forms.TimeField(
        required=False,
        label="Correct check-out time (leave empty if none)",
        widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
    )
    reason = forms.CharField(
        min_length=10,
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Explain what happened (at least 10 characters).",
    )

    def __init__(self, employee, *args, **kwargs):
        self.employee = employee
        kwargs.setdefault("initial", {"work_date": timezone.localdate() - datetime.timedelta(days=1)})
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"

    def clean(self):
        data = super().clean()
        day = data.get("work_date")
        start = data.get("check_in_time")
        if day and start:
            try:
                services.validate_correction(self.employee, day, start, data.get("check_out_time"))
            except ValidationError as error:
                for field, messages in error.message_dict.items():
                    self.add_error(field if field in self.fields else None, messages)
        return data
