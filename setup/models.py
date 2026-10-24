import datetime
from django.db import models


class Company(models.Model):
    """Only one row exists. Created during first-run setup."""
    name = models.CharField(max_length=200)
    office_start = models.TimeField(default=datetime.time(9, 0))
    grace_minutes = models.PositiveIntegerField(default=15)
    half_day_hours = models.DecimalField(max_digits=4, decimal_places=1, default=5)
    # Comma-separated weekday numbers, Monday=0 ... Sunday=6. Default Sat,Sun.
    weekend_days = models.CharField(max_length=20, default="5,6")
    office_lat = models.FloatField(null=True, blank=True)
    office_lng = models.FloatField(null=True, blank=True)
    office_radius_m = models.PositiveIntegerField(default=150)
    created_at = models.DateTimeField(auto_now_add=True)

    def weekend_list(self):
        return [int(d) for d in self.weekend_days.split(",") if d.strip() != ""]

    def __str__(self):
        return self.name


class Department(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Designation(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name


class LeaveType(models.Model):
    name = models.CharField(max_length=50, unique=True)
    code = models.CharField(max_length=20, unique=True)  # e.g. annual, sick, casual, unpaid
    yearly_quota = models.PositiveIntegerField(null=True, blank=True)  # empty = unlimited
    is_paid = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Holiday(models.Model):
    date = models.DateField(unique=True)
    name = models.CharField(max_length=100)

    def __str__(self):
        return f"{self.date} {self.name}"