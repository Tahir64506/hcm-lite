from django.conf import settings
from django.contrib.auth import authenticate
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from setup.models import Company
from setup.services import is_setup_done

from .forms import ChangePasswordForm, LoginForm
from .jwt_utils import create_token
from .middleware import COOKIE_NAME
from .permissions import jwt_login_required


def _set_auth_cookie(response, user):
    response.set_cookie(
        COOKIE_NAME,
        create_token(user),
        max_age=settings.JWT_EXPIRY_HOURS * 3600,
        httponly=True,           # JavaScript cannot read it
        samesite="Lax",
        secure=not settings.DEBUG,
    )
    return response


def login_view(request):
    if not is_setup_done():
        return redirect("first_setup")
    if request.user.is_authenticated:
        return redirect("home")

    form = LoginForm(request.POST or None)
    error = None
    if request.method == "POST" and form.is_valid():
        # username is always equal to the email
        user = authenticate(
            request,
            username=form.cleaned_data["email"],
            password=form.cleaned_data["password"],
        )
        if user is not None:
            return _set_auth_cookie(redirect("home"), user)
        error = "Invalid email or password, or the account is deactivated."
    return render(request, "accounts/login.html", {"form": form, "error": error})


@require_POST
def logout_view(request):
    response = redirect("login")
    response.delete_cookie(COOKIE_NAME)
    return response


@jwt_login_required
def change_password(request):
    form = ChangePasswordForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = request.user
        user.set_password(form.cleaned_data["new_password"])
        user.must_change_password = False
        user.save()
        return _set_auth_cookie(redirect("home"), user)
    return render(request, "accounts/change_password.html", {"form": form})


def home(request):
    if not is_setup_done():
        return redirect("first_setup")
    if not request.user.is_authenticated:
        return redirect("login")
    return render(request, "home.html", {"company": Company.objects.first()})
