from django.db import transaction

from accounts.models import User
from accounts.services import generate_temp_password
from audit.services import model_snapshot, record

from .models import Employee

EDITABLE_FIELDS = (
    "employee_code", "gender", "phone", "address", "department", "designation",
    "join_date", "status", "exit_date", "basic_salary",
)


def next_employee_code():
    numbers = []
    codes = Employee.objects.filter(employee_code__startswith="EMP-").values_list("employee_code", flat=True)
    for code in codes:
        tail = code[4:]
        if tail.isdigit():
            numbers.append(int(tail))
    return f"EMP-{(max(numbers) + 1) if numbers else 1:04d}"


def csv_safe(value):
    """Stop spreadsheet programs from running text that starts like a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@transaction.atomic
def register_employee(actor, data, existing_user=None):
    """Create an employee profile. Returns (employee, temporary_password or None)."""
    temp = None
    if existing_user is None:
        temp = generate_temp_password()
        user = User.objects.create_user(
            username=data["email"],
            email=data["email"],
            password=temp,
            first_name=data["name"],
            role=User.Role.EMPLOYEE,
            must_change_password=True,
        )
        record(actor, "create", "User", user.pk, None, model_snapshot(user))
    else:
        user = existing_user

    employee = Employee.objects.create(
        user=user,
        employee_code=data["employee_code"],
        gender=data.get("gender", ""),
        phone=data.get("phone", ""),
        address=data.get("address", ""),
        department=data["department"],
        designation=data["designation"],
        join_date=data["join_date"],
        status=data["status"],
        exit_date=data.get("exit_date"),
        basic_salary=data["basic_salary"],
    )
    record(actor, "create", "Employee", employee.pk, None, model_snapshot(employee))
    return employee, temp


@transaction.atomic
def update_employee(actor, employee, data):
    before = model_snapshot(employee)
    for field in EDITABLE_FIELDS:
        setattr(employee, field, data[field])
    employee.save()

    user = employee.user
    if data.get("name") and user.first_name != data["name"]:
        user.first_name = data["name"]
        user.save(update_fields=["first_name"])

    # Exiting locks the login. Only plain employee logins are synced; HR logins are Admin's job.
    if user.role == User.Role.EMPLOYEE:
        should_be_active = employee.status != Employee.Status.EXITED
        if user.is_active != should_be_active:
            user_before = model_snapshot(user)
            user.is_active = should_be_active
            user.save(update_fields=["is_active"])
            record(actor, "activate" if should_be_active else "deactivate", "User", user.pk,
                   user_before, model_snapshot(user))

    record(actor, "update", "Employee", employee.pk, before, model_snapshot(employee))


@transaction.atomic
def update_own_contact(actor, employee, phone, address):
    before = model_snapshot(employee)
    employee.phone = phone
    employee.address = address
    employee.save(update_fields=["phone", "address", "updated_at"])
    record(actor, "update", "Employee", employee.pk, before, model_snapshot(employee))
