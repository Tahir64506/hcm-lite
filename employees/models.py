from decimal import ROUND_HALF_UP, Decimal

from django.db import models

from accounts.models import User
from setup.models import Department, Designation

CENTS = Decimal("0.01")


def money(value):
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


class Employee(models.Model):
    class Status(models.TextChoices):
        PROBATION = "probation", "Probation"
        ACTIVE = "active", "Active"
        EXITED = "exited", "Exited"

    class Gender(models.TextChoices):
        MALE = "male", "Male"
        FEMALE = "female", "Female"
        OTHER = "other", "Other"

    user = models.OneToOneField(User, on_delete=models.PROTECT, related_name="employee")
    employee_code = models.CharField(max_length=20, unique=True)
    gender = models.CharField(max_length=10, choices=Gender.choices, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    address = models.TextField(max_length=500, blank=True)
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="employees")
    designation = models.ForeignKey(Designation, on_delete=models.PROTECT, related_name="employees")
    join_date = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PROBATION)
    exit_date = models.DateField(null=True, blank=True)
    basic_salary = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["employee_code"]

    @property
    def name(self):
        return self.user.first_name or self.user.email

    @property
    def hra(self):
        return money(self.basic_salary * Decimal("0.40"))

    @property
    def medical(self):
        return money(self.basic_salary * Decimal("0.10"))

    @property
    def gross(self):
        return money(self.basic_salary + self.hra + self.medical)

    def __str__(self):
        return f"{self.employee_code} {self.name}"
