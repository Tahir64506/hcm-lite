import datetime
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from accounts.jwt_utils import create_token
from accounts.models import User
from attendance import services
from attendance.models import Attendance
from employees.models import Employee
from setup.models import Company, Department, Designation


def at(day, hour, minute, second=0):
    """An aware datetime in November 2026, in the project time zone."""
    return timezone.make_aware(datetime.datetime(2026, 11, day, hour, minute, second))


def make_user(email, role):
    return User.objects.create_user(username=email, email=email, password="Pass#12345", role=role)


class Base(TestCase):
    def setUp(self):
        Company.objects.create(name="Test Co")
        self.dept = Department.objects.create(name="Engineering")
        self.desig = Designation.objects.create(name="Engineer")
        self.admin = make_user("admin@test.com", User.Role.ADMIN)
        self.hr = make_user("hr@test.com", User.Role.HR)
        self.employee = self.make_employee("emp@test.com", "EMP-0001")

    def make_employee(self, email, code, join_date=datetime.date(2026, 1, 1), user=None):
        return Employee.objects.create(
            user=user or make_user(email, User.Role.EMPLOYEE),
            employee_code=code,
            department=self.dept,
            designation=self.desig,
            join_date=join_date,
            basic_salary=Decimal("50000"),
        )

    def login_as(self, user):
        self.client.cookies["access_token"] = create_token(user)


class CheckInRuleTests(Base):
    def test_on_time_up_to_and_including_the_grace_limit(self):
        self.assertFalse(services.check_in(self.employee, at(23, 8, 30)).is_late)
        self.assertFalse(services.check_in(self.employee, at(24, 9, 15, 0)).is_late)

    def test_late_after_the_grace_limit(self):
        self.assertTrue(services.check_in(self.employee, at(23, 9, 15, 1)).is_late)
        self.assertTrue(services.check_in(self.employee, at(24, 10, 0)).is_late)

    def test_grace_time_comes_from_company_settings(self):
        Company.objects.update(grace_minutes=30)
        self.assertFalse(services.check_in(self.employee, at(23, 9, 29)).is_late)
        self.assertTrue(services.check_in(self.employee, at(24, 9, 31)).is_late)

    def test_work_date_follows_the_local_day(self):
        record = services.check_in(self.employee, at(23, 0, 30))
        self.assertEqual(record.work_date, datetime.date(2026, 11, 23))

    def test_second_check_in_on_the_same_day_is_rejected(self):
        services.check_in(self.employee, at(23, 9, 0))
        with self.assertRaises(services.AlreadyCheckedIn):
            services.check_in(self.employee, at(23, 10, 0))
        self.assertEqual(Attendance.objects.count(), 1)

    def test_a_new_day_allows_a_new_check_in(self):
        services.check_in(self.employee, at(23, 9, 0))
        services.check_in(self.employee, at(24, 9, 0))
        self.assertEqual(Attendance.objects.count(), 2)

    def test_cannot_check_in_before_the_join_date(self):
        early = self.make_employee("new@test.com", "EMP-0002", join_date=datetime.date(2026, 12, 1))
        with self.assertRaises(services.CheckInNotAllowed):
            services.check_in(early, at(23, 9, 0))

    def test_database_allows_one_record_per_employee_per_day(self):
        services.check_in(self.employee, at(23, 9, 0))
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Attendance.objects.create(
                    employee=self.employee, work_date=datetime.date(2026, 11, 23), check_in=at(23, 11, 0)
                )


class CheckOutRuleTests(Base):
    def test_check_out_needs_a_check_in(self):
        with self.assertRaises(services.NotCheckedIn):
            services.check_out(self.employee, at(23, 17, 0))

    def test_cannot_check_out_twice(self):
        services.check_in(self.employee, at(23, 9, 0))
        services.check_out(self.employee, at(23, 17, 0))
        with self.assertRaises(services.AlreadyCheckedOut):
            services.check_out(self.employee, at(23, 18, 0))

    def test_under_five_hours_is_a_half_day(self):
        services.check_in(self.employee, at(23, 9, 0))
        record = services.check_out(self.employee, at(23, 13, 59))
        self.assertTrue(record.is_half_day)
        self.assertEqual(record.status, "half_day")

    def test_exactly_five_hours_is_a_full_day(self):
        services.check_in(self.employee, at(24, 9, 0))
        record = services.check_out(self.employee, at(24, 14, 0))
        self.assertFalse(record.is_half_day)
        self.assertEqual(record.status, "present")

    def test_half_day_limit_comes_from_company_settings(self):
        Company.objects.update(half_day_hours=4)
        services.check_in(self.employee, at(23, 9, 0))
        self.assertFalse(services.check_out(self.employee, at(23, 13, 30)).is_half_day)

    def test_late_and_half_day_shows_as_half_day(self):
        services.check_in(self.employee, at(23, 9, 30))
        record = services.check_out(self.employee, at(23, 12, 0))
        self.assertTrue(record.is_late)
        self.assertEqual(record.status, "half_day")

    def test_late_full_day_shows_as_late(self):
        services.check_in(self.employee, at(23, 9, 30))
        record = services.check_out(self.employee, at(23, 18, 0))
        self.assertEqual(record.status, "late")

    def test_worked_time_display(self):
        services.check_in(self.employee, at(23, 9, 0))
        record = services.check_out(self.employee, at(23, 17, 30))
        self.assertEqual(record.worked_display, "8h 30m")

    def test_past_day_without_check_out_is_missing_checkout(self):
        record = services.check_in(self.employee, timezone.now() - datetime.timedelta(days=3))
        self.assertEqual(record.status, "missing_checkout")

    def test_today_without_check_out_is_not_missing(self):
        record = services.check_in(self.employee, timezone.now())
        self.assertIn(record.status, ("present", "late"))


class ViewTests(Base):
    def test_anonymous_is_sent_to_login(self):
        response = self.client.get("/attendance/")
        self.assertRedirects(response, "/login/", fetch_redirect_response=False)

    def test_admin_has_no_check_in(self):
        self.login_as(self.admin)
        self.assertEqual(self.client.get("/attendance/").status_code, 403)
        self.assertEqual(self.client.post("/attendance/check-in/").status_code, 403)

    def test_get_is_not_allowed_on_action_urls(self):
        self.login_as(self.employee.user)
        self.assertEqual(self.client.get("/attendance/check-in/").status_code, 405)
        self.assertEqual(self.client.get("/attendance/check-out/").status_code, 405)

    def test_employee_checks_in_with_server_time_and_only_for_self(self):
        other = self.make_employee("other@test.com", "EMP-0002")
        self.login_as(self.employee.user)
        response = self.client.post(
            "/attendance/check-in/",
            {"check_in": "2000-01-01 09:00", "employee": other.pk, "work_date": "2000-01-01"},
        )
        self.assertEqual(response.status_code, 302)
        record = Attendance.objects.get()
        self.assertEqual(record.employee, self.employee)
        self.assertLess(abs(timezone.now() - record.check_in), datetime.timedelta(minutes=1))
        self.assertEqual(record.work_date, timezone.localdate())

    def test_second_check_in_returns_409(self):
        self.login_as(self.employee.user)
        self.client.post("/attendance/check-in/")
        response = self.client.post("/attendance/check-in/")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Attendance.objects.count(), 1)

    def test_check_out_flow(self):
        self.login_as(self.employee.user)
        self.assertEqual(self.client.post("/attendance/check-out/").status_code, 422)
        self.client.post("/attendance/check-in/")
        self.assertEqual(self.client.post("/attendance/check-out/").status_code, 302)
        self.assertEqual(self.client.post("/attendance/check-out/").status_code, 409)
        record = Attendance.objects.get()
        self.assertIsNotNone(record.check_out)
        self.assertTrue(record.is_half_day)

    def test_hr_with_a_profile_can_check_in(self):
        self.make_employee("unused@test.com", "EMP-0900", user=self.hr)
        self.login_as(self.hr)
        self.assertEqual(self.client.post("/attendance/check-in/").status_code, 302)
        self.assertEqual(Attendance.objects.get().employee.user, self.hr)

    def test_hr_without_a_profile_cannot_check_in(self):
        self.login_as(self.hr)
        response = self.client.post("/attendance/check-in/")
        self.assertEqual(response.status_code, 422)
        self.assertFalse(Attendance.objects.exists())

    def test_page_shows_only_own_records(self):
        other = self.make_employee("other@test.com", "EMP-0002")
        services.check_in(other, at(23, 9, 0))
        self.login_as(self.employee.user)
        response = self.client.get("/attendance/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["recent"]), 0)
