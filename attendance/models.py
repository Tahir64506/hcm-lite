from django.db import models
from django.utils import timezone

from employees.models import Employee

STATUS_LABELS = {
    "present": "Present",
    "late": "Late",
    "half_day": "Half day",
    "missing_checkout": "Missing check-out",
}
STATUS_BADGES = {
    "present": "text-bg-success",
    "late": "text-bg-warning",
    "half_day": "text-bg-info",
    "missing_checkout": "text-bg-danger",
}


class Attendance(models.Model):
    class Source(models.TextChoices):
        WEB = "web", "Web check-in"
        CORRECTION = "correction", "HR correction"

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="attendance")
    work_date = models.DateField()
    check_in = models.DateTimeField()
    check_out = models.DateTimeField(null=True, blank=True)
    is_late = models.BooleanField(default=False)
    is_half_day = models.BooleanField(default=False)
    source = models.CharField(max_length=12, choices=Source.choices, default=Source.WEB)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-work_date"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "work_date"], name="one_record_per_employee_per_day"),
        ]

    @property
    def status(self):
        """Priority: missing_checkout, half_day, late, present."""
        if self.check_out is None:
            if self.work_date < timezone.localdate():
                return "missing_checkout"
            return "late" if self.is_late else "present"
        if self.is_half_day:
            return "half_day"
        return "late" if self.is_late else "present"

    @property
    def status_label(self):
        return STATUS_LABELS[self.status]

    @property
    def status_badge(self):
        return STATUS_BADGES[self.status]

    @property
    def worked_display(self):
        if self.check_out is None:
            return "-"
        minutes = int((self.check_out - self.check_in).total_seconds() // 60)
        return f"{minutes // 60}h {minutes % 60:02d}m"

    def __str__(self):
        return f"{self.employee.employee_code} {self.work_date}"
