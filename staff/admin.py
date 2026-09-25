from django.contrib import admin
from staff.models import StaffTOTPDevice, UserAssignment


@admin.register(StaffTOTPDevice)
class StaffTOTPDeviceAdmin(admin.ModelAdmin):
    list_display = ("user", "is_confirmed", "created_at", "updated_at")
    list_filter = ("is_confirmed",)
    search_fields = ("user__username", "user__email")


@admin.register(UserAssignment)
class UserAssignmentAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "region", "district", "station", "post", "active")
    list_filter = ("role", "active", "region")
    search_fields = ("user__username", "user__email")
