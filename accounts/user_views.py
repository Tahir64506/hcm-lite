from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import User
from .permissions import role_required
from .services import create_hr_user, reset_password, set_active
from .user_forms import HRUserForm


def _render_list(request, form, new_login=None):
    users = User.objects.order_by("email")
    q = request.GET.get("q", "").strip()
    role = request.GET.get("role", "")
    status = request.GET.get("status", "")
    if q:
        users = users.filter(Q(email__icontains=q) | Q(first_name__icontains=q))
    if role in dict(User.Role.choices):
        users = users.filter(role=role)
    if status == "active":
        users = users.filter(is_active=True)
    elif status == "inactive":
        users = users.filter(is_active=False)
    page = Paginator(users, 20).get_page(request.GET.get("page"))
    return render(request, "accounts/users.html", {
        "page": page,
        "form": form,
        "new_login": new_login,
        "q": q,
        "role": role,
        "status": status,
        "roles": User.Role.choices,
    })


@role_required("admin")
def user_list(request):
    form = HRUserForm(request.POST or None)
    new_login = None
    if request.method == "POST" and form.is_valid():
        user, temp = create_hr_user(request.user, form.cleaned_data["name"], form.cleaned_data["email"])
        new_login = {"email": user.email, "password": temp, "action": "created"}
        form = HRUserForm()
    return _render_list(request, form, new_login)


@role_required("admin")
@require_POST
def user_toggle(request, pk):
    user = get_object_or_404(User, pk=pk)
    try:
        set_active(request.user, user, not user.is_active)
    except ValueError as error:
        messages.error(request, str(error))
    else:
        state = "active" if user.is_active else "deactivated"
        messages.success(request, f"{user.email} is now {state}.")
    return redirect("users")


@role_required("admin")
@require_POST
def user_reset(request, pk):
    user = get_object_or_404(User, pk=pk)
    try:
        temp = reset_password(request.user, user)
    except ValueError as error:
        messages.error(request, str(error))
        return redirect("users")
    return _render_list(request, HRUserForm(), {"email": user.email, "password": temp, "action": "reset"})
