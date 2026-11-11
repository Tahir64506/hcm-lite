from django.contrib import admin
from django.urls import path

from accounts import user_views
from accounts import views as account_views
from audit import views as audit_views
from employees import views as employee_views
from setup import views as setup_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("setup/", setup_views.first_setup, name="first_setup"),
    path("login/", account_views.login_view, name="login"),
    path("logout/", account_views.logout_view, name="logout"),
    path("change-password/", account_views.change_password, name="change_password"),
    path("users/", user_views.user_list, name="users"),
    path("users/<int:pk>/toggle/", user_views.user_toggle, name="user_toggle"),
    path("users/<int:pk>/reset/", user_views.user_reset, name="user_reset"),
    path("employees/", employee_views.directory, name="employee_list"),
    path("employees/new/", employee_views.employee_new, name="employee_new"),
    path("employees/export/", employee_views.export_csv, name="employee_export"),
    path("employees/import/", employee_views.import_csv, name="employee_import"),
    path("employees/<int:pk>/", employee_views.employee_detail, name="employee_detail"),
    path("employees/<int:pk>/edit/", employee_views.employee_edit, name="employee_edit"),
    path("profile/", employee_views.my_profile, name="my_profile"),
    path("settings/", setup_views.company_settings, name="company_settings"),
    path("settings/<str:key>/", setup_views.catalog, name="catalog"),
    path("settings/<str:key>/<int:pk>/delete/", setup_views.catalog_delete, name="catalog_delete"),
    path("audit/", audit_views.audit_log, name="audit_log"),
    path("", account_views.home, name="home"),
]
