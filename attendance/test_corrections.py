import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from accounts.jwt_utils import create_token
from accounts.models import User
from attendance import services
from attendance.models import Attendance, CorrectionRequest
from audit.models import AuditLog
from employees.models import Employee
from setup.models import Company, Department, Designation

T = datetime.time
GOOD_REASON = "Forgot to check in, the page was not loading."


def make_user(email, role):
    return User.objects.create_user(username=email, email=email, password="Pass#12345", role=role)


class Base(TestCase):
    def setUp(self):
        Company.objects.create(name="Test Co")
        self.today = timezone.localdate()
        self.day = self.today - datetime.timedelta(days=3)
        self.dept = Department.objects.create(name="Engineering")
        self.desig = Designation.objects.create(name="Engineer")
        self.admin = make_user("admin@test.com", User.Role.ADMIN)
        self.hr = make_user("hr@test.com", User.Role.HR)
        self.hr2 = make_user("hr2@test.com", User.Role.HR)
        self.employee = self.make_employee("emp@test.com", "EMP-0001")

    def make_employee(self, email, code, join_days_ago=90, exit_date=None, user=None):
        return Employee.objects.create(
            user=user or make_user(email, User.Role.EMPLOYEE),
            employee_code=code,
            department=self.dept,
            designation=self.desig,
            join_date=self.today - datetime.timedelta(days=join_days_ago),
            exit_date=exit_date,
            basic_salary=Decimal("50000"),
        )

    def login_as(self, user):
        self.client.cookies["access_token"] = create_token(user)

    def aware(self, day, clock):
        return timezone.make_aware(datetime.datetime.combine(day, clock))

    def ask(self, employee=None, day=None, check_in=T(9, 0), check_out=T(17, 0), reason=GOOD_REASON, now=None):
        employee = employee or self.employee
        return services.create_correction(
            employee.user, employee, day or self.day, check_in, check_out, reason, now=now
        )


class RequestRuleTests(Base):
    def test_valid_request_is_pending_and_audited(self):
        correction = self.ask()
        self.assertEqual(correction.status, "pending")
        self.assertTrue(AuditLog.objects.filter(action="create", entity="CorrectionRequest").exists())

    def test_future_date_is_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            self.ask(day=self.today + datetime.timedelta(days=1))
        self.assertIn("work_date", ctx.exception.error_dict)

    def test_thirty_day_window(self):
        self.ask(day=self.today - datetime.timedelta(days=30))
        with self.assertRaises(ValidationError):
            self.ask(day=self.today - datetime.timedelta(days=31))

    def test_before_join_date_is_rejected(self):
        newcomer = self.make_employee("new@test.com", "EMP-0002", join_days_ago=5)
        with self.assertRaises(ValidationError):
            self.ask(employee=newcomer, day=self.today - datetime.timedelta(days=10))

    def test_after_exit_date_is_rejected(self):
        leaver = self.make_employee(
            "gone@test.com", "EMP-0003", exit_date=self.today - datetime.timedelta(days=10)
        )
        with self.assertRaises(ValidationError):
            self.ask(employee=leaver)

    def test_check_out_must_be_after_check_in(self):
        for check_out in (T(9, 0), T(8, 0)):
            with self.assertRaises(ValidationError) as ctx:
                self.ask(check_in=T(9, 0), check_out=check_out)
            self.assertIn("check_out_time", ctx.exception.error_dict)

    def test_times_today_cannot_be_in_the_future(self):
        now = self.aware(self.today, T(10, 0))
        with self.assertRaises(ValidationError) as ctx:
            self.ask(day=self.today, check_in=T(11, 0), check_out=None, now=now)
        self.assertIn("check_in_time", ctx.exception.error_dict)
        with self.assertRaises(ValidationError) as ctx:
            self.ask(day=self.today, check_in=T(9, 0), check_out=T(10, 30), now=now)
        self.assertIn("check_out_time", ctx.exception.error_dict)
        self.assertEqual(self.ask(day=self.today, check_in=T(9, 0), check_out=T(10, 0), now=now).status, "pending")

    def test_short_reason_is_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            self.ask(reason="too short")
        self.assertIn("reason", ctx.exception.error_dict)

    def test_one_pending_request_per_day(self):
        first = self.ask()
        with self.assertRaises(ValidationError):
            self.ask()
        services.cancel(self.employee.user, first.pk)
        self.assertEqual(self.ask().status, "pending")

    def test_different_days_can_be_pending_together(self):
        self.ask(day=self.day)
        self.ask(day=self.day - datetime.timedelta(days=1))
        self.assertEqual(CorrectionRequest.objects.filter(status="pending").count(), 2)


class DecisionTests(Base):
    def test_approval_creates_the_record_with_correct_flags(self):
        correction = self.ask(check_in=T(9, 30), check_out=T(18, 0))
        services.decide(self.hr, correction.pk, True, "ok")
        attendance = Attendance.objects.get()
        self.assertEqual(attendance.employee, self.employee)
        self.assertEqual(attendance.work_date, self.day)
        self.assertEqual(timezone.localtime(attendance.check_in).time(), T(9, 30))
        self.assertEqual(attendance.source, "correction")
        self.assertTrue(attendance.is_late)
        self.assertFalse(attendance.is_half_day)
        correction.refresh_from_db()
        self.assertEqual(correction.status, "approved")
        self.assertEqual(correction.decided_by, self.hr)

    def test_on_time_correction_is_not_late(self):
        correction = self.ask(check_in=T(9, 10))
        services.decide(self.hr, correction.pk, True, "")
        self.assertFalse(Attendance.objects.get().is_late)

    def test_half_day_follows_company_settings(self):
        short = self.ask(day=self.day, check_in=T(9, 0), check_out=T(12, 0))
        services.decide(self.hr, short.pk, True, "")
        full = self.ask(day=self.day - datetime.timedelta(days=1), check_in=T(9, 0), check_out=T(14, 0))
        services.decide(self.hr, full.pk, True, "")
        flags = {a.work_date: a.is_half_day for a in Attendance.objects.all()}
        self.assertTrue(flags[self.day])
        self.assertFalse(flags[self.day - datetime.timedelta(days=1)])

    def test_correction_without_check_out_shows_missing_checkout(self):
        correction = self.ask(check_out=None)
        services.decide(self.hr, correction.pk, True, "")
        attendance = Attendance.objects.get()
        self.assertIsNone(attendance.check_out)
        self.assertEqual(attendance.status, "missing_checkout")

    def test_approval_updates_an_existing_record(self):
        Attendance.objects.create(
            employee=self.employee, work_date=self.day, check_in=self.aware(self.day, T(10, 30)), is_late=True
        )
        correction = self.ask(check_in=T(9, 0), check_out=T(17, 0))
        services.decide(self.hr, correction.pk, True, "")
        self.assertEqual(Attendance.objects.count(), 1)
        attendance = Attendance.objects.get()
        self.assertFalse(attendance.is_late)
        self.assertEqual(attendance.source, "correction")
        self.assertTrue(AuditLog.objects.filter(action="update", entity="Attendance").exists())

    def test_rejection_needs_a_comment_and_creates_nothing(self):
        correction = self.ask()
        with self.assertRaises(services.CorrectionError):
            services.decide(self.hr, correction.pk, False, "   ")
        correction.refresh_from_db()
        self.assertEqual(correction.status, "pending")
        services.decide(self.hr, correction.pk, False, "Not enough detail")
        correction.refresh_from_db()
        self.assertEqual(correction.status, "rejected")
        self.assertEqual(correction.hr_comment, "Not enough detail")
        self.assertFalse(Attendance.objects.exists())

    def test_cannot_decide_twice(self):
        correction = self.ask()
        services.decide(self.hr, correction.pk, True, "")
        with self.assertRaises(services.CorrectionError):
            services.decide(self.hr, correction.pk, False, "changed my mind")

    def test_hr_cannot_decide_own_request(self):
        profile = self.make_employee("unused@test.com", "EMP-0900", user=self.hr)
        correction = self.ask(employee=profile)
        with self.assertRaises(services.CorrectionError):
            services.decide(self.hr, correction.pk, True, "")
        services.decide(self.hr2, correction.pk, True, "")
        self.assertEqual(Attendance.objects.get().employee, profile)

    def test_approval_fails_if_employee_left_before_that_day(self):
        correction = self.ask()
        Employee.objects.filter(pk=self.employee.pk).update(exit_date=self.day - datetime.timedelta(days=1))
        with self.assertRaises(services.CorrectionError):
            services.decide(self.hr, correction.pk, True, "")
        correction.refresh_from_db()
        self.assertEqual(correction.status, "pending")
        self.assertFalse(Attendance.objects.exists())

    def test_decisions_are_audited(self):
        correction = self.ask()
        services.decide(self.hr, correction.pk, True, "")
        self.assertTrue(AuditLog.objects.filter(action="approve", entity="CorrectionRequest").exists())
        self.assertTrue(AuditLog.objects.filter(action="create", entity="Attendance").exists())

    def test_cancel_rules(self):
        correction = self.ask()
        with self.assertRaises(services.CorrectionError):
            services.cancel(self.hr, correction.pk)
        services.cancel(self.employee.user, correction.pk)
        correction.refresh_from_db()
        self.assertEqual(correction.status, "cancelled")
        with self.assertRaises(services.CorrectionError):
            services.decide(self.hr, correction.pk, True, "")
        with self.assertRaises(services.CorrectionError):
            services.cancel(self.employee.user, correction.pk)

    def test_approved_correction_for_today_blocks_a_second_check_in(self):
        now = self.aware(self.today, T(23, 59))
        correction = self.ask(day=self.today, check_in=T(8, 0), check_out=None, now=now)
        services.decide(self.hr, correction.pk, True, "", now=now)
        with self.assertRaises(services.AlreadyCheckedIn):
            services.check_in(self.employee)


class ViewTests(Base):
    def setUp(self):
        super().setUp()
        self.other = self.make_employee("other@test.com", "EMP-0002")
        self.form = {
            "work_date": self.day.isoformat(),
            "check_in_time": "09:00",
            "check_out_time": "17:00",
            "reason": GOOD_REASON,
        }

    def decide_url(self, correction):
        return f"/attendance/corrections/{correction.pk}/decide/"

    def test_anonymous_is_sent_to_login(self):
        for url in ("/attendance/corrections/", "/attendance/corrections/review/"):
            self.assertRedirects(self.client.get(url), "/login/", fetch_redirect_response=False)

    def test_role_access(self):
        self.login_as(self.admin)
        self.assertEqual(self.client.get("/attendance/corrections/").status_code, 403)
        self.assertEqual(self.client.get("/attendance/corrections/review/").status_code, 200)
        self.login_as(self.employee.user)
        self.assertEqual(self.client.get("/attendance/corrections/").status_code, 200)
        self.assertEqual(self.client.get("/attendance/corrections/review/").status_code, 403)
        self.login_as(self.hr)
        self.assertEqual(self.client.get("/attendance/corrections/").status_code, 200)
        self.assertEqual(self.client.get("/attendance/corrections/review/").status_code, 200)

    def test_employee_creates_a_request_for_self_only(self):
        self.login_as(self.employee.user)
        response = self.client.post("/attendance/corrections/", {**self.form, "employee": self.other.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CorrectionRequest.objects.get().employee, self.employee)

    def test_invalid_and_duplicate_posts_show_errors(self):
        self.login_as(self.employee.user)
        response = self.client.post("/attendance/corrections/", {**self.form, "reason": "short"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("reason", response.context["form"].errors)
        self.assertFalse(CorrectionRequest.objects.exists())
        self.client.post("/attendance/corrections/", self.form)
        response = self.client.post("/attendance/corrections/", self.form)
        self.assertIn("work_date", response.context["form"].errors)
        self.assertEqual(CorrectionRequest.objects.count(), 1)

    def test_employee_sees_only_own_requests(self):
        self.ask(employee=self.other)
        mine = self.ask()
        self.login_as(self.employee.user)
        items = list(self.client.get("/attendance/corrections/").context["items"])
        self.assertEqual(items, [mine])

    def test_hr_approves_through_the_view(self):
        correction = self.ask()
        self.login_as(self.hr)
        response = self.client.post(self.decide_url(correction), {"decision": "approve", "comment": ""})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Attendance.objects.filter(employee=self.employee, work_date=self.day).exists())

    def test_reject_without_comment_keeps_it_pending(self):
        correction = self.ask()
        self.login_as(self.hr)
        self.client.post(self.decide_url(correction), {"decision": "reject", "comment": ""})
        correction.refresh_from_db()
        self.assertEqual(correction.status, "pending")

    def test_only_hr_can_decide(self):
        correction = self.ask()
        for user in (self.employee.user, self.admin):
            self.login_as(user)
            response = self.client.post(self.decide_url(correction), {"decision": "approve"})
            self.assertEqual(response.status_code, 403)
        correction.refresh_from_db()
        self.assertEqual(correction.status, "pending")
        self.login_as(self.hr)
        self.assertEqual(self.client.get(self.decide_url(correction)).status_code, 405)
        self.assertEqual(self.client.post(self.decide_url(correction), {"decision": "nonsense"}).status_code, 400)

    def test_cancel_through_the_view(self):
        correction = self.ask()
        url = f"/attendance/corrections/{correction.pk}/cancel/"
        self.login_as(self.other.user)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.login_as(self.employee.user)
        self.assertEqual(self.client.post(url).status_code, 302)
        correction.refresh_from_db()
        self.assertEqual(correction.status, "cancelled")

    def test_review_defaults_to_pending_and_can_show_all(self):
        done = self.ask(day=self.day - datetime.timedelta(days=1))
        services.decide(self.hr, done.pk, True, "")
        self.ask()
        self.login_as(self.hr)
        self.assertEqual(len(self.client.get("/attendance/corrections/review/").context["rows"]), 1)
        self.assertEqual(len(self.client.get("/attendance/corrections/review/?status=").context["rows"]), 2)
