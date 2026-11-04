from django.contrib import admin
from django.urls import path

from accounts import views as account_views
from audit import views as audit_views
from setup import views as setup_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("setup/", setup_views.first_setup, name="first_setup"),
    path("login/", account_views.login_view, name="login"),
    path("logout/", account_views.logout_view, name="logout"),
    path("change-password/", account_views.change_password, name="change_password"),
    path("settings/", setup_views.company_settings, name="company_settings"),
    path("settings/<str:key>/", setup_views.catalog, name="catalog"),
    path("settings/<str:key>/<int:pk>/delete/", setup_views.catalog_delete, name="catalog_delete"),
    path("audit/", audit_views.audit_log, name="audit_log"),
    path("", account_views.home, name="home"),
]
