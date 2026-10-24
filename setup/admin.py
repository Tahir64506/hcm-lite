from django.contrib import admin
from .models import Company, Department, Designation, LeaveType, Holiday

admin.site.register([Company, Department, Designation, LeaveType, Holiday])