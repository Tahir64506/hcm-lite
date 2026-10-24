from django.shortcuts import redirect, render

from setup.models import Company
from setup.services import is_setup_done


def home(request):
    if not is_setup_done():
        return redirect("first_setup")
    return render(request, "home.html", {"company": Company.objects.first()})