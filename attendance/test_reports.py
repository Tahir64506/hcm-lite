import datetime
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from accounts.jwt_utils import create_token
from accounts.models import User
from attendance import reports
from attendance.models import Attendance
from employees.models import Employee
from setup.models import Company, Department, Designation, Holiday

TODAY = datetime.date(2026, 11, 25)  # a Wednesday
WEEKEND = {5, 6}


def d(day):
    return datetime.date(2026, 11, day)


def at(day, hour, minute=0):
    return timezone.make_aware(datetime.datetime(2026, 11, day, hour, minute))


def make_user(email, role):
    return User.objects.create_user(username=email, email=email, password="Pass#12345", role=role)


class Base(TestCase):
    def setUp(self):
        Company.objects.create(name="Test Co")
        self.dept = Department.objects.create(name="Engineering")
        self.sales = Department.objects.create(name="Sales")
        self.desig = Designation.objects.create(name="Engineer")
        self.admin = make_user("admin@test.com", User.Role.ADMIN)
        self.hr = make_user("hr@test.com", User.Role.HR)
        self.employee = self.make_employee("emp@test.com", "EMP-0001")

    def make_employee(self, email, code, join_date=datetime.date(2026, 1, 1), exit_date=None, department=None):
        return Employee.objects.create(
            user=make_user(email, User.Role.EMPLOYEE),
            employee_code=code,
            department=department or self.dept,
            designation=self.desig,
            join_date=join_date,
            exit_date=exit_date,
            basic_salary=Decimal("50000"),
        )

    def record(self, employee, day, out_hour=17, late=False):
        return Attendance.objects.create(
            employee=employee,
            work_date=d(day),
            check_in=at(day, 9),
            check_out=at(day, out_hour) if out_hour is not None else None,
            is_late=late,
        )

    def login_as(self, user):
        self.client.cookies["access_token"] = create_token(user)


class DayStatusTests(Base):
    def status(self, day, record=None, employee=None, holidays=None, leave=()):
        return reports.day_status(employee or self.employee, d(day), record, TODAY, WEEKEND, holidays or {}, leave)

    def test_weekend(self):
        self.assertEqual(self.status(21), "weekend")

    def test_holiday(self):
        self.assertEqual(self.status(24, holidays={d(24): "Test Day"}), "holiday")

    def test_past_working_day_without_record_is_absent(self):
        self.assertEqual(self.status(23), "absent")

    def test_today_without_record_is_pending(self):
        self.assertEqual(self.status(25), "pending")

    def test_future_day(self):
        self.assertEqual(self.status(26), "future")

    def test_before_join_date(self):
        joiner = self.make_employee("new@test.com", "EMP-0002", join_date=d(24))
        self.assertEqual(self.status(23, employee=joiner), "before_join")
        self.assertEqual(self.status(24, employee=joiner), "absent")

    def test_after_exit_date(self):
        leaver = self.make_employee("gone@test.com", "EMP-0003", exit_date=d(20))
        self.assertEqual(self.status(23, employee=leaver), "after_exit")
        self.assertEqual(self.status(20, employee=leaver), "absent")

    def test_approved_leave_is_not_absent(self):
        self.assertEqual(self.status(23, leave={d(23)}), "on_leave")

    def test_weekend_beats_leave(self):
        self.assertEqual(self.status(21, leave={d(21)}), "weekend")

    def test_record_decides_the_status(self):
        self.assertEqual(self.status(23, record=self.record(self.employee, 23)), "present")
        self.assertEqual(self.status(24, record=self.record(self.employee, 24, late=True)), "late")

    def test_work_on_a_weekend_shows_the_record(self):
        self.assertEqual(self.status(21, record=self.record(self.employee, 21)), "present")

    def test_old_record_without_check_out_is_missing_checkout(self):
        self.assertEqual(self.status(23, record=self.record(self.employee, 23, out_hour=None)), "missing_checkout")

    def test_todays_record_without_check_out_is_not_missing(self):
        self.assertEqual(self.status(25, record=self.record(self.employee, 25, out_hour=None)), "present")


class MonthCalendarTests(Base):
    def test_empty_month_counts(self):
        weeks, counts = reports.month_calendar(self.employee, 2026, 11, TODAY)
        self.assertEqual(counts["weekend"], 9)
        self.assertEqual(counts["absent"], 17)
        self.assertEqual(counts["pending"], 1)
        self.assertEqual(counts["future"], 3)
        self.assertTrue(all(len(week) == 7 for week in weeks))
        self.assertIsNone(weeks[0][0])
        self.assertEqual(weeks[0][6]["date"], d(1))

    def test_records_and_holidays_change_the_counts(self):
        self.record(self.employee, 23)
        self.record(self.employee, 24, late=True)
        Holiday.objects.create(date=d(20), name="Test Day")
        _, counts = reports.month_calendar(self.employee, 2026, 11, TODAY)
        self.assertEqual(counts["present"], 1)
        self.assertEqual(counts["late"], 1)
        self.assertEqual(counts["holiday"], 1)
        self.assertEqual(counts["absent"], 14)

    def test_weekend_setting_comes_from_company(self):
        Company.objects.update(weekend_days="4,5")  # Friday and Saturday
        _, counts = reports.month_calendar(self.employee, 2026, 11, TODAY)
        self.assertEqual(counts["weekend"], 8)


class BoardTests(Base):
    def setUp(self):
        super().setUp()
        self.salesman = self.make_employee("sales@test.com", "EMP-0002", department=self.sales)
        self.late_joiner = self.make_employee("late@test.com", "EMP-0003", join_date=d(24))
        self.leaver = self.make_employee("gone@test.com", "EMP-0004", exit_date=d(20))

    def codes(self, rows):
        return {r["employee"].employee_code for r in rows}

    def test_only_people_employed_on_that_day_are_listed(self):
        rows, _, _, _ = reports.board(d(23), TODAY)
        self.assertEqual(self.codes(rows), {"EMP-0001", "EMP-0002"})
        rows, _, _, _ = reports.board(d(24), TODAY)
        self.assertEqual(self.codes(rows), {"EMP-0001", "EMP-0002", "EMP-0003"})

    def test_exit_day_itself_is_listed(self):
        rows, _, _, _ = reports.board(d(20), TODAY)
        self.assertIn("EMP-0004", self.codes(rows))

    def test_counts(self):
        self.record(self.employee, 23)
        _, counts, _, _ = reports.board(d(23), TODAY)
        self.assertEqual(counts["present"], 1)
        self.assertEqual(counts["absent"], 1)

    def test_department_filter(self):
        rows, _, _, _ = reports.board(d(23), TODAY, self.sales.pk)
        self.assertEqual(self.codes(rows), {"EMP-0002"})

    def test_weekend_and_holiday_flags(self):
        rows, _, holiday, weekend = reports.board(d(21), TODAY)
        self.assertTrue(weekend)
        self.assertTrue(all(r["status"] == "weekend" for r in rows))
        Holiday.objects.create(date=d(23), name="Test Day")
        rows, _, holiday, weekend = reports.board(d(23), TODAY)
        self.assertEqual(holiday, "Test Day")
        self.assertFalse(weekend)
        self.assertTrue(all(r["status"] == "holiday" for r in rows))

    def test_approved_leave_is_shown(self):
        with mock.patch("attendance.reports.approved_leave_days", return_value={self.employee.pk: {d(23)}}):
            _, counts, _, _ = reports.board(d(23), TODAY)
        self.assertEqual(counts["on_leave"], 1)
        self.assertEqual(counts["absent"], 1)


class BoardViewTests(Base):
    def test_anonymous_is_sent_to_login(self):
        response = self.client.get("/attendance/board/")
        self.assertRedirects(response, "/login/", fetch_redirect_response=False)

    def test_employee_cannot_open_the_board(self):
        self.login_as(self.employee.user)
        self.assertEqual(self.client.get("/attendance/board/").status_code, 403)

    def test_admin_and_hr_can_open_the_board(self):
        for user in (self.admin, self.hr):
            self.login_as(user)
            self.assertEqual(self.client.get("/attendance/board/").status_code, 200)

    def test_bad_and_future_dates_fall_back_to_today(self):
        self.login_as(self.hr)
        today = timezone.localdate()
        response = self.client.get("/attendance/board/?date=not-a-date")
        self.assertEqual(response.context["day"], today)
        self.assertTrue(response.context["notice"])
        future = (today + datetime.timedelta(days=5)).isoformat()
        response = self.client.get(f"/attendance/board/?date={future}")
        self.assertEqual(response.context["day"], today)
        self.assertTrue(response.context["notice"])

    def test_status_filter(self):
        today = timezone.localdate()
        Attendance.objects.create(employee=self.employee, work_date=today, check_in=timezone.now())
        self.login_as(self.hr)
        response = self.client.get("/attendance/board/?status=present")
        self.assertEqual(len(response.context["page"].object_list), 1)
        response = self.client.get("/attendance/board/?status=absent")
        self.assertEqual(len(response.context["page"].object_list), 0)
        response = self.client.get("/attendance/board/?status=nonsense")
        self.assertEqual(response.context["status"], "")


class CalendarViewTests(Base):
    def setUp(self):
        super().setUp()
        self.other = self.make_employee("other@test.com", "EMP-0002")

    def url(self, employee):
        return f"/attendance/calendar/{employee.pk}/"

    def test_anonymous_is_sent_to_login(self):
        response = self.client.get(self.url(self.employee))
        self.assertRedirects(response, "/login/", fetch_redirect_response=False)

    def test_employee_sees_only_their_own_calendar(self):
        self.login_as(self.employee.user)
        self.assertEqual(self.client.get(self.url(self.employee)).status_code, 200)
        self.assertEqual(self.client.get(self.url(self.other)).status_code, 403)

    def test_hr_and_admin_can_open_any_calendar(self):
        for user in (self.hr, self.admin):
            self.login_as(user)
            self.assertEqual(self.client.get(self.url(self.other)).status_code, 200)

    def test_future_and_invalid_months_are_clamped_to_this_month(self):
        self.login_as(self.hr)
        today = timezone.localdate()
        for value in ("2999-01", "garbage", "2026-13"):
            response = self.client.get(self.url(self.employee) + f"?month={value}")
            self.assertEqual((response.context["year"], response.context["month"]), (today.year, today.month))

    def test_months_before_the_join_date_are_clamped(self):
        self.login_as(self.hr)
        response = self.client.get(self.url(self.employee) + "?month=2021-03")
        self.assertEqual((response.context["year"], response.context["month"]), (2026, 1))
        self.assertIsNone(response.context["prev_month"])

    def test_my_calendar_redirects_to_own_calendar(self):
        self.login_as(self.employee.user)
        response = self.client.get("/attendance/calendar/")
        self.assertRedirects(response, self.url(self.employee), fetch_redirect_response=False)

    def test_admin_has_no_own_calendar(self):
        self.login_as(self.admin)
        self.assertEqual(self.client.get("/attendance/calendar/").status_code, 403)
