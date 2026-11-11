from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from accounts.jwt_utils import create_token, get_user_from_token
from accounts.models import User
from audit.models import AuditLog
from employees import importer
from employees.models import Employee
from setup.models import Company, Department, Designation

HEADER = "employee_code,name,email,department,designation,join_date,basic_salary\n"


def make_user(email, role, password="Pass#12345", name=""):
    return User.objects.create_user(
        username=email, email=email, password=password, role=role, first_name=name
    )


class Base(TestCase):
    def setUp(self):
        Company.objects.create(name="Test Co")
        self.today = timezone.localdate().isoformat()
        self.admin = make_user("admin@test.com", User.Role.ADMIN)
        self.hr = make_user("hr@test.com", User.Role.HR, name="HR One")
        self.dept = Department.objects.create(name="Engineering")
        self.desig = Designation.objects.create(name="Engineer")

    def login_as(self, user):
        self.client.cookies["access_token"] = create_token(user)

    def make_employee(self, email="emp@test.com", code="EMP-0001", salary="50000"):
        user = make_user(email, User.Role.EMPLOYEE, name="Ali Raza")
        return Employee.objects.create(
            user=user, employee_code=code, department=self.dept, designation=self.desig,
            join_date=timezone.localdate(), basic_salary=Decimal(salary),
        )

    def form_data(self, **overrides):
        data = {
            "name": "Sara Ahmed", "email": "sara@test.com", "employee_code": "EMP-0100",
            "gender": "female", "phone": "", "address": "", "department": self.dept.pk,
            "designation": self.desig.pk, "join_date": self.today, "status": "probation",
            "exit_date": "", "basic_salary": "60000",
        }
        data.update(overrides)
        return data


class RegistrationTests(Base):
    def test_hr_registers_employee_with_temporary_login(self):
        self.login_as(self.hr)
        response = self.client.post("/employees/new/", self.form_data())
        self.assertEqual(response.status_code, 200)
        employee = Employee.objects.get(employee_code="EMP-0100")
        self.assertEqual(employee.user.role, User.Role.EMPLOYEE)
        self.assertTrue(employee.user.must_change_password)
        self.assertTrue(employee.user.check_password(response.context["temp_password"]))
        self.assertTrue(AuditLog.objects.filter(action="create", entity="Employee").exists())

    def test_code_is_stored_in_upper_case(self):
        self.login_as(self.hr)
        self.client.post("/employees/new/", self.form_data(employee_code="emp-0101"))
        self.assertTrue(Employee.objects.filter(employee_code="EMP-0101").exists())

    def test_admin_cannot_register(self):
        self.login_as(self.admin)
        self.assertEqual(self.client.get("/employees/new/").status_code, 403)
        self.client.post("/employees/new/", self.form_data())
        self.assertFalse(Employee.objects.exists())

    def test_duplicate_email_and_code_are_rejected(self):
        self.make_employee(email="sara@test.com", code="EMP-0100")
        self.login_as(self.hr)
        self.client.post("/employees/new/", self.form_data())
        self.client.post("/employees/new/", self.form_data(email="other@test.com"))
        self.assertEqual(Employee.objects.count(), 1)

    def test_bad_salary_is_rejected(self):
        self.login_as(self.hr)
        for salary in ("0", "-5", "abc", "10.555"):
            self.client.post("/employees/new/", self.form_data(basic_salary=salary))
        self.assertFalse(Employee.objects.exists())

    def test_existing_hr_login_can_be_linked(self):
        self.login_as(self.hr)
        users_before = User.objects.count()
        response = self.client.post(
            "/employees/new/",
            self.form_data(existing_user=self.hr.pk, name="", email="", employee_code="EMP-0200"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["temp_password"])
        self.assertEqual(User.objects.count(), users_before)
        self.assertEqual(Employee.objects.get(employee_code="EMP-0200").user, self.hr)


class AccessTests(Base):
    def test_anonymous_is_sent_to_login(self):
        response = self.client.get("/employees/")
        self.assertRedirects(response, "/login/", fetch_redirect_response=False)

    def test_employee_cannot_open_directory_or_other_profiles(self):
        mine = self.make_employee()
        other = self.make_employee(email="other@test.com", code="EMP-0002")
        self.login_as(mine.user)
        self.assertEqual(self.client.get("/employees/").status_code, 403)
        self.assertEqual(self.client.get(f"/employees/{other.pk}/").status_code, 403)
        self.assertEqual(self.client.get(f"/employees/{mine.pk}/").status_code, 200)

    def test_admin_and_hr_can_view_directory_and_profiles(self):
        employee = self.make_employee()
        for user in (self.admin, self.hr):
            self.login_as(user)
            self.assertEqual(self.client.get("/employees/").status_code, 200)
            self.assertEqual(self.client.get(f"/employees/{employee.pk}/").status_code, 200)

    def test_employee_edits_only_phone_and_address(self):
        employee = self.make_employee()
        self.login_as(employee.user)
        response = self.client.post(
            f"/employees/{employee.pk}/",
            {"phone": "+92 300 1234567", "address": "Karachi", "basic_salary": "999999", "department": "999"},
        )
        self.assertEqual(response.status_code, 302)
        employee.refresh_from_db()
        self.assertEqual(employee.phone, "+92 300 1234567")
        self.assertEqual(employee.basic_salary, Decimal("50000.00"))
        self.assertTrue(AuditLog.objects.filter(action="update", entity="Employee").exists())

    def test_invalid_phone_is_rejected(self):
        employee = self.make_employee()
        self.login_as(employee.user)
        self.client.post(f"/employees/{employee.pk}/", {"phone": "abc", "address": ""})
        employee.refresh_from_db()
        self.assertEqual(employee.phone, "")

    def test_employee_and_admin_cannot_edit_employees(self):
        employee = self.make_employee()
        for user in (employee.user, self.admin):
            self.login_as(user)
            self.assertEqual(self.client.get(f"/employees/{employee.pk}/edit/").status_code, 403)

    def test_admin_cannot_edit_someone_elses_contact_details(self):
        employee = self.make_employee()
        self.login_as(self.admin)
        response = self.client.post(f"/employees/{employee.pk}/", {"phone": "+92 300 1234567", "address": "x"})
        self.assertEqual(response.status_code, 403)

    def test_search_and_filters(self):
        self.make_employee(email="ali@test.com", code="EMP-0001")
        self.make_employee(email="zara@test.com", code="EMP-0002")
        self.login_as(self.hr)
        response = self.client.get("/employees/?q=zara")
        self.assertEqual(response.context["page"].paginator.count, 1)
        response = self.client.get("/employees/?status=active")
        self.assertEqual(response.context["page"].paginator.count, 0)


class StatusTests(Base):
    def edit_data(self, employee, **overrides):
        return self.form_data(name="Ali Raza", employee_code=employee.employee_code, **overrides)

    def test_exit_locks_the_login(self):
        employee = self.make_employee()
        token = create_token(employee.user)
        self.login_as(self.hr)
        self.client.post(
            f"/employees/{employee.pk}/edit/",
            self.edit_data(employee, status="exited", exit_date=self.today),
        )
        employee.refresh_from_db()
        employee.user.refresh_from_db()
        self.assertEqual(employee.status, "exited")
        self.assertFalse(employee.user.is_active)
        self.assertIsNone(get_user_from_token(token))

    def test_reactivating_unlocks_the_login(self):
        employee = self.make_employee()
        self.login_as(self.hr)
        self.client.post(
            f"/employees/{employee.pk}/edit/",
            self.edit_data(employee, status="exited", exit_date=self.today),
        )
        self.client.post(f"/employees/{employee.pk}/edit/", self.edit_data(employee, status="active"))
        employee.user.refresh_from_db()
        self.assertTrue(employee.user.is_active)

    def test_exit_needs_an_exit_date(self):
        employee = self.make_employee()
        self.login_as(self.hr)
        self.client.post(f"/employees/{employee.pk}/edit/", self.edit_data(employee, status="exited"))
        employee.refresh_from_db()
        self.assertEqual(employee.status, "probation")

    def test_exit_date_cannot_be_before_join_date(self):
        employee = self.make_employee()
        self.login_as(self.hr)
        self.client.post(
            f"/employees/{employee.pk}/edit/",
            self.edit_data(employee, status="exited", exit_date="2000-01-01"),
        )
        employee.refresh_from_db()
        self.assertEqual(employee.status, "probation")

    def test_hr_cannot_exit_an_hr_profile(self):
        profile = Employee.objects.create(
            user=self.hr, employee_code="EMP-0900", department=self.dept, designation=self.desig,
            join_date=timezone.localdate(), basic_salary=Decimal("80000"),
        )
        self.login_as(self.hr)
        self.client.post(
            f"/employees/{profile.pk}/edit/",
            self.form_data(name="HR One", employee_code="EMP-0900", status="exited", exit_date=self.today),
        )
        self.hr.refresh_from_db()
        profile.refresh_from_db()
        self.assertTrue(self.hr.is_active)
        self.assertEqual(profile.status, "probation")


class SalaryTests(Base):
    def test_breakdown_matches_the_payroll_formula(self):
        employee = self.make_employee(salary="50000")
        self.assertEqual(employee.hra, Decimal("20000.00"))
        self.assertEqual(employee.medical, Decimal("5000.00"))
        self.assertEqual(employee.gross, Decimal("75000.00"))


class CsvCheckTests(Base):
    def test_errors_report_row_field_and_reason(self):
        content = HEADER + f"EMP-0300,Test,not-an-email,Nope,Engineer,15/01/2026,0\n"
        result = importer.check_csv(content.encode())
        fields = {field for _, field, _ in result.errors}
        self.assertEqual(fields, {"email", "department", "join_date", "basic_salary"})
        self.assertTrue(all(row == 2 for row, _, _ in result.errors))
        self.assertTrue(all(reason for _, _, reason in result.errors))
        self.assertEqual(result.rows, [])

    def test_required_fields_are_checked(self):
        result = importer.check_csv((HEADER + ",,,,,,\n,,x,,,,\n").encode())
        self.assertIn((3, "employee_code", "Required, but empty."), result.errors)

    def test_duplicates_inside_the_file_are_flagged(self):
        content = (
            HEADER
            + f"EMP-0301,A,a@test.com,Engineering,Engineer,{self.today},50000\n"
            + f"EMP-0301,B,a@test.com,Engineering,Engineer,{self.today},50000\n"
        )
        result = importer.check_csv(content.encode())
        self.assertEqual({(row, field) for row, field, _ in result.errors},
                         {(3, "employee_code"), (3, "email")})
        self.assertEqual(len(result.rows), 1)

    def test_existing_code_and_email_are_flagged(self):
        self.make_employee(email="taken@test.com", code="EMP-0001")
        content = HEADER + f"emp-0001,A,taken@test.com,Engineering,Engineer,{self.today},50000\n"
        result = importer.check_csv(content.encode())
        self.assertEqual({field for _, field, _ in result.errors}, {"employee_code", "email"})

    def test_missing_column_is_fatal(self):
        result = importer.check_csv(b"employee_code,name\nEMP-1,A\n")
        self.assertIn("basic_salary", result.fatal)

    def test_empty_file_is_fatal(self):
        self.assertIsNotNone(importer.check_csv(HEADER.encode()).fatal)


class CsvViewTests(Base):
    def upload(self, content, dry_run=True, name="employees.csv"):
        data = {"file": SimpleUploadedFile(name, content.encode(), content_type="text/csv")}
        if dry_run:
            data["dry_run"] = "on"
        return self.client.post("/employees/import/", data)

    def good_row(self, code="EMP-0400", email="new1@test.com"):
        return f"{code},New One,{email},Engineering,Engineer,{self.today},50000\n"

    def test_dry_run_saves_nothing(self):
        self.login_as(self.hr)
        response = self.upload(HEADER + self.good_row(), dry_run=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["result"].rows), 1)
        self.assertFalse(Employee.objects.exists())

    def test_real_import_creates_employees_with_temporary_logins(self):
        self.login_as(self.hr)
        content = HEADER + self.good_row() + self.good_row("EMP-0401", "new2@test.com")
        response = self.upload(content, dry_run=False)
        self.assertEqual(Employee.objects.count(), 2)
        self.assertEqual(len(response.context["logins"]), 2)
        employee, temp = response.context["logins"][0]
        self.assertTrue(employee.user.check_password(temp))
        self.assertTrue(employee.user.must_change_password)

    def test_one_bad_row_blocks_the_whole_import(self):
        self.login_as(self.hr)
        content = HEADER + self.good_row() + "EMP-0402,Bad,nope,Engineering,Engineer,2026-01-01,50000\n"
        response = self.upload(content, dry_run=False)
        self.assertFalse(Employee.objects.exists())
        self.assertTrue(response.context["errors"])

    def test_only_hr_can_import(self):
        self.login_as(self.admin)
        self.assertEqual(self.client.get("/employees/import/").status_code, 403)
        self.assertEqual(self.upload(HEADER + self.good_row(), dry_run=False).status_code, 403)
        self.assertFalse(Employee.objects.exists())

    def test_non_csv_file_is_rejected(self):
        self.login_as(self.hr)
        response = self.upload(HEADER + self.good_row(), dry_run=False, name="employees.txt")
        self.assertIn("file", response.context["form"].errors)
        self.assertFalse(Employee.objects.exists())


class ExportTests(Base):
    def test_only_hr_can_export(self):
        employee = self.make_employee()
        for user in (employee.user, self.admin):
            self.login_as(user)
            self.assertEqual(self.client.get("/employees/export/").status_code, 403)

    def test_hr_exports_csv_and_it_is_logged(self):
        self.make_employee()
        self.login_as(self.hr)
        response = self.client.get("/employees/export/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/csv"))
        self.assertIn("EMP-0001", response.content.decode())
        self.assertTrue(AuditLog.objects.filter(action="export", entity="Employee").exists())

    def test_formula_text_is_neutralised(self):
        employee = self.make_employee()
        employee.user.first_name = "=SUM(1+1)"
        employee.user.save()
        self.login_as(self.hr)
        content = self.client.get("/employees/export/").content.decode()
        self.assertIn("'=SUM(1+1)", content)


class CatalogProtectionTests(Base):
    def test_department_with_employees_cannot_be_deleted(self):
        self.make_employee()
        self.login_as(self.admin)
        self.client.post(f"/settings/departments/{self.dept.pk}/delete/")
        self.assertTrue(Department.objects.filter(pk=self.dept.pk).exists())
