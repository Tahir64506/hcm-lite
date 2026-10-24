from django.shortcuts import redirect, render

from .forms import FirstSetupForm
from .services import is_setup_done, run_first_setup


def first_setup(request):
    if is_setup_done():
        return redirect("home")

    form = FirstSetupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        run_first_setup(
            company_name=form.cleaned_data["company_name"],
            admin_name=form.cleaned_data["admin_name"],
            email=form.cleaned_data["email"],
            password=form.cleaned_data["password"],
        )
        return redirect("home")
    return render(request, "setup/first_setup.html", {"form": form})