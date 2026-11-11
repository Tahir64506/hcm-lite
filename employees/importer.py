import csv
import datetime
import io
import re

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from accounts.models import User
from setup.models import Department, Designation

from . import validators
from .models import Employee
from .services import register_employee

MAX_ROWS = 500
REQUIRED = ["employee_code", "name", "email", "department", "designation", "join_date", "basic_salary"]
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
GENDERS = {"male", "female", "other"}


class ImportResult:
    def __init__(self):
        self.rows = []      # valid rows, ready to import
        self.errors = []    # (row number, field, reason)
        self.total = 0
        self.fatal = None   # message when the file cannot be checked at all


def _check_row(line_no, row, ctx, errors):
    start = len(errors)

    def fail(field, reason):
        errors.append((line_no, field, reason))

    for column in REQUIRED:
        if not row.get(column):
            fail(column, "Required, but empty.")

    data = {"exit_date": None}

    raw_code = row.get("employee_code", "")
    if raw_code:
        try:
            code = validators.clean_code(raw_code)
        except ValidationError as error:
            fail("employee_code", error.messages[0])
        else:
            if code in ctx["used_codes"]:
                fail("employee_code", "This code already exists.")
            elif code in ctx["seen_codes"]:
                fail("employee_code", f"Same code as row {ctx['seen_codes'][code]} in this file.")
            else:
                ctx["seen_codes"][code] = line_no
            data["employee_code"] = code

    email = row.get("email", "").lower()
    if email:
        try:
            validate_email(email)
        except ValidationError:
            fail("email", "Not a valid email address.")
        else:
            if email in ctx["used_emails"]:
                fail("email", "This email already has a login.")
            elif email in ctx["seen_emails"]:
                fail("email", f"Same email as row {ctx['seen_emails'][email]} in this file.")
            else:
                ctx["seen_emails"][email] = line_no
            data["email"] = email

    name = row.get("name", "")
    if name:
        if len(name) > 100:
            fail("name", "Too long (maximum 100 characters).")
        data["name"] = name

    department_name = row.get("department", "")
    if department_name:
        department = ctx["departments"].get(department_name.lower())
        if department is None:
            fail("department", f"Unknown department '{department_name}'. Add it in Setup first.")
        data["department"] = department

    designation_name = row.get("designation", "")
    if designation_name:
        designation = ctx["designations"].get(designation_name.lower())
        if designation is None:
            fail("designation", f"Unknown designation '{designation_name}'. Add it in Setup first.")
        data["designation"] = designation

    joined = row.get("join_date", "")
    if joined:
        if not ISO_DATE.fullmatch(joined):
            fail("join_date", "Use the format YYYY-MM-DD.")
        else:
            try:
                data["join_date"] = validators.clean_join_date(datetime.date.fromisoformat(joined))
            except ValueError:
                fail("join_date", "Not a real calendar date.")
            except ValidationError as error:
                fail("join_date", error.messages[0])

    salary = row.get("basic_salary", "")
    if salary:
        try:
            data["basic_salary"] = validators.clean_salary(salary)
        except ValidationError as error:
            fail("basic_salary", error.messages[0])

    gender = row.get("gender", "").lower()
    if gender and gender not in GENDERS:
        fail("gender", "Use male, female or other.")
    data["gender"] = gender if gender in GENDERS else ""

    try:
        data["phone"] = validators.clean_phone(row.get("phone", ""))
    except ValidationError as error:
        fail("phone", error.messages[0])

    address = row.get("address", "")
    if len(address) > 500:
        fail("address", "Too long (maximum 500 characters).")
    data["address"] = address

    status = row.get("status", "").lower() or Employee.Status.PROBATION
    if status not in (Employee.Status.PROBATION, Employee.Status.ACTIVE):
        fail("status", "Use probation or active.")
    data["status"] = status

    return data if len(errors) == start else None


def check_csv(raw):
    """Validate a CSV file without saving anything."""
    result = ImportResult()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        result.fatal = "The file must be UTF-8 text. Save it as CSV UTF-8 and try again."
        return result

    reader = csv.DictReader(io.StringIO(text))
    try:
        headers = [(h or "").strip().lower() for h in (reader.fieldnames or [])]
    except csv.Error as error:
        result.fatal = f"The file could not be read as CSV: {error}"
        return result
    missing = [c for c in REQUIRED if c not in headers]
    if missing:
        result.fatal = "Missing column(s): " + ", ".join(missing)
        return result
    reader.fieldnames = headers

    ctx = {
        "departments": {d.name.lower(): d for d in Department.objects.filter(is_active=True)},
        "designations": {d.name.lower(): d for d in Designation.objects.all()},
        "used_codes": {c.upper() for c in Employee.objects.values_list("employee_code", flat=True)},
        "used_emails": {e.lower() for e in User.objects.values_list("email", flat=True)},
        "seen_codes": {},
        "seen_emails": {},
    }

    try:
        for line_no, raw_row in enumerate(reader, start=2):
            row = {key: (value or "").strip() for key, value in raw_row.items() if key}
            if not any(row.values()):
                continue
            result.total += 1
            if result.total > MAX_ROWS:
                result.fatal = f"Too many rows (maximum {MAX_ROWS} per file)."
                return result
            data = _check_row(line_no, row, ctx, result.errors)
            if data is not None:
                result.rows.append(data)
    except csv.Error as error:
        result.fatal = f"The file could not be read as CSV: {error}"
        return result

    if result.total == 0:
        result.fatal = "The file has no data rows."
    return result


@transaction.atomic
def import_rows(actor, rows):
    """All rows or none. Returns a list of (employee, temporary_password)."""
    return [register_employee(actor, data) for data in rows]
