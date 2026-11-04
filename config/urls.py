from django.contrib import admin
from django.urls import path

from accounts import views as account_views
from setup.views import first_setup

urlpatterns = [
    path("admin/", admin.site.urls),
    path("setup/", first_setup, name="first_setup"),
    path("login/", account_views.login_view, name="login"),
    path("logout/", account_views.logout_view, name="logout"),
    path("change-password/", account_views.change_password, name="change_password"),
    path("", account_views.home, name="home"),
]
