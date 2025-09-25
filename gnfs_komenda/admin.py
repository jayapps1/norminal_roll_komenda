from django.contrib import admin
from gnfs_komenda.models import NorminalRoll
# Register your models here.


@admin.register(NorminalRoll)
class NorminallRollAdmin(admin.ModelAdmin):
    list_display = ['staffID','fullname', 'payroll_status', 'leave_status', 'job_grade']
    search_fields = ['staffID']
    ordering = ['-created_at']