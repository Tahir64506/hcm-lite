from django.test import TestCase

from accounts.jwt_utils import create_token, get_user_from_token
from accounts.models import User
from accounts.services import reset_password, set_active
from audit.models import AuditLog
from setup.models import Company


def make_user(email, role, password="Pass#12345"):
    return User.objects.create_user(username=email, email=email, password=password, role=role)


class UserManagementTests(TestCase):
    def setUp(self):
        Company.objects.create(name="Test Co")
        self.admin = make_user("admin@test.com", User.Role.ADMIN)
        self.hr = make_user("hr@test.com", User.Role.HR)

    def login_as(self, user):
        self.client.cookies["access_token"] = create_token(user)

    def test_anonymous_is_sent_to_login(self):
        response = self.client.get("/users/")
        self.assertRedirects(response, "/login/", fetch_redirect_response=False)

    def test_hr_cannot_open_users_page(self):
        self.login_as(self.hr)
        self.assertEqual(self.client.get("/users/").status_code, 403)

    def test_admin_creates_hr_with_temporary_password(self):
        self.login_as(self.admin)
        response = self.client.post("/users/", {"name": "New HR", "email": "new.hr@test.com"})
        self.assertEqual(response.status_code, 200)
        created = User.objects.get(email="new.hr@test.com")
        self.assertEqual(created.role, User.Role.HR)
        self.assertTrue(created.must_change_password)
        log = AuditLog.objects.get(action="create", entity="User", entity_id=str(created.pk))
        self.assertNotIn("password", log.after)

    def test_duplicate_email_is_rejected(self):
        self.login_as(self.admin)
        self.client.post("/users/", {"name": "First", "email": "dup@test.com"})
        self.client.post("/users/", {"name": "Second", "email": "DUP@test.com"})
        self.assertEqual(User.objects.filter(email__iexact="dup@test.com").count(), 1)

    def test_deactivated_user_token_stops_working(self):
        token = create_token(self.hr)
        self.assertIsNotNone(get_user_from_token(token))
        set_active(self.admin, self.hr, False)
        self.assertIsNone(get_user_from_token(token))

    def test_admin_cannot_deactivate_self(self):
        with self.assertRaises(ValueError):
            set_active(self.admin, self.admin, False)

    def test_reset_forces_password_change(self):
        temp = reset_password(self.admin, self.hr)
        self.hr.refresh_from_db()
        self.assertTrue(self.hr.must_change_password)
        self.assertTrue(self.hr.check_password(temp))
        self.assertFalse(self.hr.check_password("Pass#12345"))

    def test_cannot_reset_own_password_here(self):
        with self.assertRaises(ValueError):
            reset_password(self.admin, self.admin)

    def test_forced_change_blocks_other_pages(self):
        self.hr.must_change_password = True
        self.hr.save()
        self.login_as(self.hr)
        response = self.client.get("/settings/")
        self.assertRedirects(response, "/change-password/", fetch_redirect_response=False)
