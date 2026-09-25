from django.contrib import admin
from gnfs_komenda.models import (
    Region, District, Station, Post, Rank, NorminalRoll,
    PersonnelActionRequest, ApprovalHistory, AuditLog, Notification,
    LeaveType, PublicHoliday, LeaveEntitlement, LeaveRequest,
    LeaveApprovalDecision, LeaveDefermentRequest, LeaveAdjustmentAudit
)


@admin.register(Rank)
class RankAdmin(admin.ModelAdmin):
    list_display = ['name', 'abbreviation', 'officer_category', 'hierarchy_order', 'active']
    list_filter = ['officer_category', 'active']
    search_fields = ['name', 'abbreviation']
    ordering = ['hierarchy_order']


@admin.register(Region)
class RegionAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'active', 'created_at']
    list_filter = ['active']
    search_fields = ['name', 'code']
    ordering = ['name']


@admin.register(District)
class DistrictAdmin(admin.ModelAdmin):
    list_display = ['name', 'region', 'code', 'active']
    list_filter = ['active', 'region']
    search_fields = ['name', 'code', 'region__name']
    ordering = ['region__name', 'name']


@admin.register(Station)
class StationAdmin(admin.ModelAdmin):
    list_display = ['name', 'district', 'code', 'station_type', 'active']
    list_filter = ['active', 'station_type', 'district__region']
    search_fields = ['name', 'code', 'district__name']
    ordering = ['district__name', 'name']


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ['name', 'station', 'code', 'post_type', 'active']
    list_filter = ['active', 'post_type', 'station__district__region']
    search_fields = ['name', 'code', 'station__name']
    ordering = ['station__name', 'name']


@admin.register(NorminalRoll)
class NorminalRollAdmin(admin.ModelAdmin):
    list_display = ['staffID', 'fullname', 'station', 'post', 'rank', 'officer_category', 'leave_status', 'payroll_status', 'is_active']
    list_filter = ['is_active', 'officer_category', 'rank', 'leave_status', 'payroll_status', 'station__district__region', 'station']
    search_fields = ['staffID', 'fullname', 'ghana_card', 'station__name']
    ordering = ['fullname']


@admin.register(PersonnelActionRequest)
class PersonnelActionRequestAdmin(admin.ModelAdmin):
    list_display = ['id', 'personnel', 'action_type', 'requested_by', 'status', 'current_approval_level', 'created_at']
    list_filter = ['status', 'current_approval_level', 'action_type']
    search_fields = ['personnel__fullname', 'personnel__staffID', 'requested_by__username']
    ordering = ['-created_at']


@admin.register(ApprovalHistory)
class ApprovalHistoryAdmin(admin.ModelAdmin):
    list_display = ['request', 'approver', 'approval_level', 'decision', 'timestamp']
    list_filter = ['approval_level', 'decision']
    search_fields = ['request__personnel__fullname', 'approver__username']
    ordering = ['-timestamp']


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ['timestamp', 'user', 'action', 'target_type', 'target_id', 'region', 'station']
    list_filter = ['action', 'target_type', 'region']
    search_fields = ['description', 'user__username', 'target_id']
    ordering = ['-timestamp']


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['recipient', 'title', 'is_read', 'created_at']
    list_filter = ['is_read', 'notification_type']
    search_fields = ['recipient__username', 'title', 'message']
    ordering = ['-created_at']


# ==============================================================================
# LEAVE MANAGEMENT ADMIN
# ==============================================================================

@admin.register(LeaveType)
class LeaveTypeAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'calculation_mode', 'default_days_junior', 'default_days_senior', 'calendar_months', 'is_deferrable', 'active']
    list_filter = ['calculation_mode', 'is_deferrable', 'active']
    search_fields = ['name', 'code']
    ordering = ['name']


@admin.register(PublicHoliday)
class PublicHolidayAdmin(admin.ModelAdmin):
    list_display = ['name', 'date', 'year', 'active']
    list_filter = ['year', 'active']
    search_fields = ['name']
    ordering = ['date']


@admin.register(LeaveEntitlement)
class LeaveEntitlementAdmin(admin.ModelAdmin):
    list_display = ['personnel', 'leave_year', 'leave_type', 'base_entitlement', 'carried_forward', 'adjustments', 'days_taken', 'days_reserved', 'remaining_balance']
    list_filter = ['leave_year', 'leave_type']
    search_fields = ['personnel__staffID', 'personnel__fullname']
    ordering = ['-leave_year', 'personnel__fullname']


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ['id', 'personnel', 'leave_type', 'start_date', 'last_leave_date', 'duration', 'working_days_count', 'resume_date', 'status', 'is_recalled']
    list_filter = ['status', 'leave_type', 'calculation_mode', 'is_recalled']
    search_fields = ['personnel__staffID', 'personnel__fullname', 'reason']
    ordering = ['-created_at']


@admin.register(LeaveApprovalDecision)
class LeaveApprovalDecisionAdmin(admin.ModelAdmin):
    list_display = ['leave_request', 'approver', 'approver_role', 'decision', 'timestamp']
    list_filter = ['decision', 'approver_role']
    search_fields = ['approver__username', 'comments']
    ordering = ['-timestamp']


@admin.register(LeaveDefermentRequest)
class LeaveDefermentRequestAdmin(admin.ModelAdmin):
    list_display = ['id', 'personnel', 'leave_year', 'carried_into_year', 'days_requested', 'approved_days', 'status', 'is_carried_forward']
    list_filter = ['status', 'is_carried_forward', 'leave_year']
    search_fields = ['personnel__staffID', 'personnel__fullname']
    ordering = ['-created_at']


@admin.register(LeaveAdjustmentAudit)
class LeaveAdjustmentAuditAdmin(admin.ModelAdmin):
    list_display = ['entitlement', 'adjusted_by', 'days_adjusted', 'previous_balance', 'new_balance', 'timestamp']
    list_filter = ['timestamp']
    search_fields = ['entitlement__personnel__fullname', 'adjusted_by__username', 'reason']
    ordering = ['-timestamp']