import datetime
from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from accounts.jwt_utils import create_token
from accounts.models import User
from attendance import reports
from attendance.models import Attendance
from audit.models import AuditLog
from employees.models import Employee
from leave import services
from leave.models import LeaveRequest
from setup.models import Company, Department, Designation, Holiday, LeaveType

NOW = timezone.make_aware(datetime.datetime(2026, 11, 20, 10, 0))  # a Friday


def d(month, day, year=2026):
    return datetime.date(year, month, day)


def make_user(email, role):
    return User.objects.create_user(username=email, email=email, password="Pass#12345", role=role)


class Base(TestCase):
    def setUp(self):
        Company.objects.create(name="Test Co")
        self.dept = Department.objects.create(name="Engineering")
        self.desig = Designation.objects.create(name="Engineer")
        self.annual = LeaveType.objects.create(name="Annual Leave", code="annual", yearly_quota=14)
        self.sick = LeaveType.objects.create(name="Sick Leave", code="sick", yearly_quota=8)
        self.casual = LeaveType.objects.create(name="Casual Leave", code="casual", yearly_quota=10)
        self.unpaid = LeaveType.objects.create(name="Unpaid Leave", code="unpaid", yearly_quota=None, is_paid=False)
        self.admin = make_user("admin@test.com", User.Role.ADMIN)
        self.hr = make_user("hr@test.com", User.Role.HR)
        self.hr2 = make_user("hr2@test.com", User.Role.HR)
        self.employee = self.make_employee("emp@test.com", "EMP-0001")

    def make_employee(self, email, code, join_date=datetime.date(2026, 1, 1), exit_date=None, user=None):
        return Employee.objects.create(
            user=user or make_user(email, User.Role.EMPLOYEE),
            employee_code=code,
            department=self.dept,
            designation=self.desig,
            join_date=join_date,
            exit_date=exit_date,
            basic_salary=Decimal("50000"),
        )

    def login_as(self, user):
        self.client.cookies["access_token"] = create_token(user)

    def apply(self, leave_type=None, start=d(11, 23), end=d(11, 27), employee=None,
              reason="Family wedding in Lahore", now=NOW):
        employee = employee or self.employee
        return services.create_request(employee.user, employee, leave_type or self.annual, start, end, reason, now=now)


class WorkingDaysTests(Base):
    def test_weekends_are_skipped(self):
        self.assertEqual(services.working_days(d(11, 23), d(11, 29)), 5)
        self.assertEqual(services.working_days(d(11, 27), d(11, 30)), 2)

    def test_holidays_are_skipped(self):
        Holiday.objects.create(date=d(11, 25), name="Test Day")
        self.assertEqual(services.working_days(d(11, 23), d(11, 27)), 4)

    def test_weekend_setting_comes_from_company(self):
        Company.objects.update(weekend_days="4,5")  # Friday and Saturday
        self.assertEqual(services.working_days(d(11, 27), d(11, 29)), 1)


class RequestRuleTests(Base):
    def test_valid_request_is_pending_and_audited(self):
        leave = self.apply()
        self.assertEqual(leave.status, "pending")
        self.assertEqual(leave.days, 5)
        self.assertTrue(AuditLog.objects.filter(action="create", entity="LeaveRequest").exists())

    def test_range_without_working_days_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.apply(start=d(11, 28), end=d(11, 29))

    def test_end_before_start_is_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            self.apply(start=d(11, 27), end=d(11, 23))
        self.assertIn("end_date", ctx.exception.error_dict)

    def test_must_stay_within_one_calendar_year(self):
        with self.assertRaises(ValidationError):
            self.apply(start=d(12, 30), end=d(1, 2, 2027))

    def test_span_limit(self):
        with self.assertRaises(ValidationError):
            self.apply(leave_type=self.unpaid, start=d(10, 21), end=d(12, 31))

    def test_backdating_window(self):
        self.apply(start=d(10, 21), end=d(10, 21))  # exactly 30 days back, a Wednesday
        with self.assertRaises(ValidationError) as ctx:
            self.apply(start=d(10, 20), end=d(10, 20))
        self.assertIn("start_date", ctx.exception.error_dict)

    def test_cannot_book_more_than_a_year_ahead(self):
        with self.assertRaises(ValidationError) as ctx:
            self.apply(start=d(12, 1, 2027), end=d(12, 1, 2027))
        self.assertIn("end_date", ctx.exception.error_dict)

    def test_before_join_date_and_after_exit_date(self):
        newcomer = self.make_employee("new@test.com", "EMP-0002", join_date=d(11, 25))
        with self.assertRaises(ValidationError):
            self.apply(employee=newcomer)
        leaver = self.make_employee("gone@test.com", "EMP-0003", exit_date=d(11, 25))
        with self.assertRaises(ValidationError):
            self.apply(employee=leaver)

    def test_short_reason_is_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            self.apply(reason="abc")
        self.assertIn("reason", ctx.exception.error_dict)


class OverlapTests(Base):
    def test_overlap_with_pending_and_approved_is_rejected(self):
        first = self.apply()
        with self.assertRaises(ValidationError):
            self.apply(leave_type=self.casual, start=d(11, 26), end=d(11, 30))
        services.decide(self.hr, first.pk, True, "", now=NOW)
        with self.assertRaises(ValidationError):
            self.apply(start=d(11, 27), end=d(11, 27))

    def test_neighbouring_days_are_allowed(self):
        self.apply()
        self.apply(start=d(11, 30), end=d(12, 1))
        self.assertEqual(LeaveRequest.objects.count(), 2)

    def test_rejected_and_cancelled_requests_do_not_block(self):
        first = self.apply()
        services.decide(self.hr, first.pk, False, "No cover that week", now=NOW)
        second = self.apply()
        services.cancel(self.employee.user, second.pk, now=NOW)
        self.assertEqual(self.apply().status, "pending")

    def test_other_employees_do_not_block_each_other(self):
        other = self.make_employee("other@test.com", "EMP-0002")
        self.apply()
        self.apply(employee=other)
        self.assertEqual(LeaveRequest.objects.count(), 2)


class BalanceTests(Base):
    def test_pending_requests_use_up_the_balance(self):
        self.apply(leave_type=self.sick)  # 5 days
        with self.assertRaises(ValidationError) as ctx:
            self.apply(leave_type=self.sick, start=d(11, 30), end=d(12, 4))  # 5 more would make 10
        self.assertIn("leave_type", ctx.exception.error_dict)
        self.apply(leave_type=self.sick, start=d(11, 30), end=d(12, 2))  # 3 more makes exactly 8
        with self.assertRaises(ValidationError):
            self.apply(leave_type=self.sick, start=d(12, 7), end=d(12, 7))

    def test_unlimited_type_has_no_limit(self):
        leave = self.apply(leave_type=self.unpaid, start=d(11, 23), end=d(12, 18))
        self.assertEqual(leave.days, 20)

    def test_cancelling_releases_the_balance(self):
        first = self.apply(leave_type=self.sick, start=d(11, 23), end=d(12, 2))  # 8 days
        with self.assertRaises(ValidationError):
            self.apply(leave_type=self.sick, start=d(12, 7), end=d(12, 7))
        services.cancel(self.employee.user, first.pk, now=NOW)
        self.assertEqual(self.apply(leave_type=self.sick, start=d(12, 7), end=d(12, 7)).days, 1)

    def test_each_year_has_its_own_balance(self):
        self.apply(leave_type=self.sick)  # 5 days in 2026
        leave = self.apply(leave_type=self.sick, start=d(1, 4, 2027), end=d(1, 8, 2027))
        self.assertEqual(leave.days, 5)

    def test_balance_row_numbers(self):
        approved = self.apply(leave_type=self.sick, start=d(11, 23), end=d(11, 25))  # 3 days
        services.decide(self.hr, approved.pk, True, "", now=NOW)
        self.apply(leave_type=self.sick, start=d(11, 30), end=d(12, 1))  # 2 days pending
        row = services.balance_row(self.employee, self.sick, 2026)
        self.assertEqual((row["quota"], row["approved"], row["pending"], row["remaining"]), (8, 3, 2, 3))
        self.assertIsNone(services.balance_row(self.employee, self.unpaid, 2026)["remaining"])


class DecisionTests(Base):
    def test_approval_records_the_decision(self):
        leave = self.apply()
        services.decide(self.hr, leave.pk, True, "Enjoy", now=NOW)
        leave.refresh_from_db()
        self.assertEqual(leave.status, "approved")
        self.assertEqual(leave.decided_by, self.hr)
        self.assertEqual(leave.hr_comment, "Enjoy")
        self.assertTrue(AuditLog.objects.filter(action="approve", entity="LeaveRequest").exists())

    def test_rejection_needs_a_comment(self):
        leave = self.apply()
        with self.assertRaises(services.LeaveError):
            services.decide(self.hr, leave.pk, False, "  ", now=NOW)
        services.decide(self.hr, leave.pk, False, "Team is short that week", now=NOW)
        leave.refresh_from_db()
        self.assertEqual(leave.status, "rejected")

    def test_cannot_decide_twice(self):
        leave = self.apply()
        services.decide(self.hr, leave.pk, True, "", now=NOW)
        with self.assertRaises(services.LeaveError) as ctx:
            services.decide(self.hr, leave.pk, False, "changed my mind", now=NOW)
        self.assertEqual(ctx.exception.status, 409)

    def test_hr_cannot_decide_own_request(self):
        profile = self.make_employee("unused@test.com", "EMP-0900", user=self.hr)
        leave = self.apply(employee=profile)
        with self.assertRaises(services.LeaveError):
            services.decide(self.hr, leave.pk, True, "", now=NOW)
        services.decide(self.hr2, leave.pk, True, "", now=NOW)
        leave.refresh_from_db()
        self.assertEqual(leave.status, "approved")

    def test_approval_rechecks_the_quota(self):
        leave = self.apply(leave_type=self.sick)  # 5 days
        LeaveType.objects.filter(pk=self.sick.pk).update(yearly_quota=3)
        with self.assertRaises(services.LeaveError):
            services.decide(self.hr, leave.pk, True, "", now=NOW)
        leave.refresh_from_db()
        self.assertEqual(leave.status, "pending")

    def test_employee_cancels_own_pending_request(self):
        leave = self.apply()
        with self.assertRaises(services.LeaveError):
            services.cancel(self.hr, leave.pk, now=NOW)
        services.cancel(self.employee.user, leave.pk, now=NOW)
        leave.refresh_from_db()
        self.assertEqual(leave.status, "cancelled")
        with self.assertRaises(services.LeaveError):
            services.cancel(self.employee.user, leave.pk, now=NOW)

    def test_upcoming_approved_leave_can_be_cancelled_but_not_started_leave(self):
        upcoming = self.apply()
        services.decide(self.hr, upcoming.pk, True, "", now=NOW)
        services.cancel(self.employee.user, upcoming.pk, now=NOW)
        started = self.apply(start=d(11, 18), end=d(11, 19))
        services.decide(self.hr, started.pk, True, "", now=NOW)
        with self.assertRaises(services.LeaveError):
            services.cancel(self.employee.user, started.pk, now=NOW)


class AttendanceLinkTests(Base):
    def approved(self, start=d(11, 23), end=d(11, 27)):
        leave = self.apply(start=start, end=end)
        services.decide(self.hr, leave.pk, True, "", now=NOW)
        return leave

    def test_board_shows_on_leave(self):
        self.approved()
        rows, counts, _, _ = reports.board(d(11, 23), d(11, 30))
        self.assertEqual(counts["on_leave"], 1)
        self.assertEqual(rows[0]["status"], "on_leave")

    def test_pending_or_rejected_leave_is_not_on_leave(self):
        leave = self.apply()
        _, counts, _, _ = reports.board(d(11, 23), d(11, 30))
        self.assertEqual(counts["on_leave"], 0)
        self.assertEqual(counts["absent"], 1)
        services.decide(self.hr, leave.pk, False, "No", now=NOW)
        _, counts, _, _ = reports.board(d(11, 23), d(11, 30))
        self.assertEqual(counts["on_leave"], 0)

    def test_attendance_record_wins_over_leave(self):
        self.approved()
        Attendance.objects.create(
            employee=self.employee,
            work_date=d(11, 23),
            check_in=timezone.make_aware(datetime.datetime(2026, 11, 23, 9, 0)),
            check_out=timezone.make_aware(datetime.datetime(2026, 11, 23, 17, 0)),
        )
        rows, _, _, _ = reports.board(d(11, 23), d(11, 30))
        self.assertEqual(rows[0]["status"], "present")

    def test_calendar_counts_leave_days_but_not_weekends(self):
        self.approved(start=d(11, 23), end=d(11, 30))
        _, counts = reports.month_calendar(self.employee, 2026, 11, d(11, 30))
        self.assertEqual(counts["on_leave"], 6)


class ViewTests(Base):
    def setUp(self):
        super().setUp()
        patcher = mock.patch("django.utils.timezone.now", return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.other = self.make_employee("other@test.com", "EMP-0002")
        self.form = {
            "leave_type": self.annual.pk,
            "start_date": "2026-11-23",
            "end_date": "2026-11-27",
            "reason": "Family wedding in Lahore",
        }

    def decide_url(self, leave):
        return f"/leave/{leave.pk}/decide/"

    def test_anonymous_is_sent_to_login(self):
        for url in ("/leave/", "/leave/review/"):
            self.assertRedirects(self.client.get(url), "/login/", fetch_redirect_response=False)

    def test_role_access(self):
        self.login_as(self.admin)
        self.assertEqual(self.client.get("/leave/").status_code, 403)
        self.assertEqual(self.client.get("/leave/review/").status_code, 200)
        self.login_as(self.employee.user)
        self.assertEqual(self.client.get("/leave/").status_code, 200)
        self.assertEqual(self.client.get("/leave/review/").status_code, 403)
        self.login_as(self.hr)
        self.assertEqual(self.client.get("/leave/").status_code, 200)
        self.assertEqual(self.client.get("/leave/review/").status_code, 200)

    def test_employee_applies_for_self_only(self):
        self.login_as(self.employee.user)
        response = self.client.post("/leave/", {**self.form, "employee": self.other.pk})
        self.assertEqual(response.status_code, 302)
        leave = LeaveRequest.objects.get()
        self.assertEqual(leave.employee, self.employee)
        self.assertEqual(leave.days, 5)

    def test_overlap_returns_422(self):
        self.apply()
        self.login_as(self.employee.user)
        response = self.client.post("/leave/", self.form)
        self.assertEqual(response.status_code, 422)
        self.assertIn("start_date", response.context["form"].errors)
        self.assertEqual(LeaveRequest.objects.count(), 1)

    def test_over_balance_returns_422(self):
        self.login_as(self.employee.user)
        response = self.client.post(
            "/leave/", {**self.form, "leave_type": self.sick.pk, "end_date": "2026-12-04"}
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("leave_type", response.context["form"].errors)
        self.assertFalse(LeaveRequest.objects.exists())

    def test_invalid_input_returns_422(self):
        self.login_as(self.employee.user)
        for bad in ({"reason": "no"}, {"start_date": "garbage"}, {"leave_type": "999"}):
            response = self.client.post("/leave/", {**self.form, **bad})
            self.assertEqual(response.status_code, 422)
        self.assertFalse(LeaveRequest.objects.exists())

    def test_employee_sees_only_own_history(self):
        self.apply(employee=self.other)
        mine = self.apply()
        self.login_as(self.employee.user)
        response = self.client.get("/leave/")
        self.assertEqual([r.pk for r in response.context["items"]], [mine.pk])

    def test_hr_approves_through_the_view(self):
        leave = self.apply()
        self.login_as(self.hr)
        response = self.client.post(self.decide_url(leave), {"decision": "approve", "comment": ""})
        self.assertEqual(response.status_code, 302)
        leave.refresh_from_db()
        self.assertEqual(leave.status, "approved")

    def test_reject_without_comment_keeps_it_pending(self):
        leave = self.apply()
        self.login_as(self.hr)
        self.client.post(self.decide_url(leave), {"decision": "reject", "comment": ""})
        leave.refresh_from_db()
        self.assertEqual(leave.status, "pending")

    def test_only_hr_can_decide(self):
        leave = self.apply()
        for user in (self.employee.user, self.admin):
            self.login_as(user)
            self.assertEqual(self.client.post(self.decide_url(leave), {"decision": "approve"}).status_code, 403)
        leave.refresh_from_db()
        self.assertEqual(leave.status, "pending")
        self.login_as(self.hr)
        self.assertEqual(self.client.get(self.decide_url(leave)).status_code, 405)
        self.assertEqual(self.client.post(self.decide_url(leave), {"decision": "nonsense"}).status_code, 400)

    def test_cancel_through_the_view(self):
        leave = self.apply()
        url = f"/leave/{leave.pk}/cancel/"
        self.login_as(self.other.user)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.login_as(self.employee.user)
        self.assertEqual(self.client.post(url).status_code, 302)
        leave.refresh_from_db()
        self.assertEqual(leave.status, "cancelled")

    def test_review_defaults_to_pending_and_can_show_all(self):
        done = self.apply()
        services.decide(self.hr, done.pk, True, "", now=NOW)
        self.apply(start=d(11, 30), end=d(12, 1))
        self.login_as(self.hr)
        self.assertEqual(len(self.client.get("/leave/review/").context["rows"]), 1)
        self.assertEqual(len(self.client.get("/leave/review/?status=").context["rows"]), 2)

    def test_leave_type_in_use_cannot_be_deleted(self):
        self.apply()
        self.login_as(self.admin)
        self.client.post(f"/settings/leave-types/{self.annual.pk}/delete/")
        self.assertTrue(LeaveType.objects.filter(pk=self.annual.pk).exists())
