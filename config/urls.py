from django.contrib import admin
from django.urls import path

from accounts.views import home
from setup.views import first_setup

urlpatterns = [
    path("admin/", admin.site.urls),
    path("setup/", first_setup, name="first_setup"),
    path("", home, name="home"),
]