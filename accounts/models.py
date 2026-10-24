from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        HR = "hr", "HR"
        EMPLOYEE = "employee", "Employee"

    # We log in with email; username is always set equal to the email.
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.EMPLOYEE)
    must_change_password = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.email} ({self.role})"