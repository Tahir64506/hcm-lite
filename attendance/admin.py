from django.contrib import admin

from .models import Attendance, CorrectionRequest

admin.site.register([Attendance, CorrectionRequest])
