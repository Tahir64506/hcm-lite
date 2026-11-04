from django.db import transaction

from accounts.models import User
from audit.services import model_snapshot, record

from .models import Company


def is_setup_done():
    return Company.objects.exists()


@transaction.atomic
def run_first_setup(company_name, admin_name, email, password):
    if Company.objects.exists():
        raise ValueError("Setup has already been completed.")
    company = Company.objects.create(name=company_name)
    admin = User.objects.create_user(
        username=email,
        email=email,
        password=password,
        first_name=admin_name,
        role=User.Role.ADMIN,
        is_staff=True,
        is_superuser=True,  # lets us use Django's admin panel for debugging
    )
    record(admin, "create", "Company", company.pk, None, model_snapshot(company))
    record(admin, "create", "User", admin.pk, None, model_snapshot(admin))
    return company, admin
