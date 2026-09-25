from django.urls import path
from staff.views import stafflogin
from gnfs_komenda.views import (
    AllPersonnel, AddStaff, DeleteStaff, EditStaff, StaffView,
    ExportAllPersonnelExcel, ImportPersonnelExcel,
    officers_on_leave, officers_at_post,
    NationalDashboardView, RegionalDashboardView, DistrictDashboardView,
    StationDashboardView, PostDashboardView,
    api_regions, api_districts, api_stations, api_posts, api_ranks, api_personnel_search,
    approval_list_view, approval_detail_view,
    notifications_view, audit_logs_view,
    structure_manage_view, add_region, edit_region,
    add_district, edit_district, add_station, edit_station,
    add_post, edit_post, toggle_unit_active, delete_unit,
    api_structure_search,
    api_leave_calculate, api_leave_entitlement,
    leave_dashboard_view, leave_apply_view, leave_detail_view,
    leave_approve_view, leave_reject_view, leave_recall_view,
    leave_balances_view, leave_adjust_balance_view, leave_calendar_view,
    leave_deferments_list_view, leave_deferment_apply_view,
    leave_deferment_approve_view, leave_deferment_reject_view,
    public_holidays_view, public_holiday_add_view,
    public_holiday_toggle_view, public_holiday_delete_view
)

urlpatterns = [
    # Core Roster & Auth Entrance
    path('', stafflogin, name='login-staff'),
    path('personnel/', AllPersonnel.as_view(), name='personnel'),
    path('add/staff', AddStaff.as_view(), name='addstaff'),
    path('delete/staff/<str:staffID>/', DeleteStaff.as_view(), name='deletestaff'),
    path('edit/staff/<str:staffID>/', EditStaff.as_view(), name='editstaff'),
    path("staff/details/<str:staffID>/", StaffView.as_view(), name="staff-detail"),
    path('export/personnel/', ExportAllPersonnelExcel.as_view(), name='export-personnel'),
    path("import/personnel/", ImportPersonnelExcel.as_view(), name="import-personnel"),
    path('officers_on_leave/', officers_on_leave, name='officers_on_leave'),
    path('officers_at_post/', officers_at_post, name='officers_at_post'),

    # Hierarchical Dashboards
    path('dashboard/national/', NationalDashboardView.as_view(), name='dashboard-national'),
    path('dashboard/region/<int:region_id>/', RegionalDashboardView.as_view(), name='dashboard-regional'),
    path('dashboard/district/<int:district_id>/', DistrictDashboardView.as_view(), name='dashboard-district'),
    path('dashboard/station/<int:station_id>/', StationDashboardView.as_view(), name='dashboard-station'),
    path('dashboard/post/<int:post_id>/', PostDashboardView.as_view(), name='dashboard-post'),

    # Dynamic Cascading Dropdowns & Live Search APIs
    path('api/regions/', api_regions, name='api-regions'),
    path('api/districts/', api_districts, name='api-districts'),
    path('api/stations/', api_stations, name='api-stations'),
    path('api/posts/', api_posts, name='api-posts'),
    path('api/ranks/', api_ranks, name='api-ranks'),
    path('api/personnel/search/', api_personnel_search, name='api-personnel-search'),

    # Multi-Tier Approval Workflow
    path('approvals/', approval_list_view, name='approval-list'),
    path('approvals/<int:request_id>/', approval_detail_view, name='approval-detail'),

    # In-App Notifications & Audit Logs
    path('notifications/', notifications_view, name='notifications'),
    path('audit-logs/', audit_logs_view, name='audit-logs'),

    # In-App Dynamic Structure Management Portal (17 Regions, Districts, Stations, Posts)
    path('structure/', structure_manage_view, name='structure-manage'),
    path('structure/region/add/', add_region, name='add-region'),
    path('structure/region/<int:pk>/edit/', edit_region, name='edit-region'),
    path('structure/district/add/', add_district, name='add-district'),
    path('structure/district/<int:pk>/edit/', edit_district, name='edit-district'),
    path('structure/station/add/', add_station, name='add-station'),
    path('structure/station/<int:pk>/edit/', edit_station, name='edit-station'),
    path('structure/post/add/', add_post, name='add-post'),
    path('structure/post/<int:pk>/edit/', edit_post, name='edit-post'),
    path('structure/<str:unit_type>/<int:pk>/toggle/', toggle_unit_active, name='toggle-unit-active'),
    path('structure/<str:unit_type>/<int:pk>/delete/', delete_unit, name='delete-unit'),
    path('structure/api/search/', api_structure_search, name='api-structure-search'),

    # Enterprise Leave Management Module
    path('leave/', leave_dashboard_view, name='leave-dashboard'),
    path('leave/apply/', leave_apply_view, name='leave-apply'),
    path('leave/<int:pk>/', leave_detail_view, name='leave-detail'),
    path('leave/<int:pk>/approve/', leave_approve_view, name='leave-approve'),
    path('leave/<int:pk>/reject/', leave_reject_view, name='leave-reject'),
    path('leave/<int:pk>/recall/', leave_recall_view, name='leave-recall'),
    path('leave/balances/', leave_balances_view, name='leave-balances'),
    path('leave/balances/<int:entitlement_id>/adjust/', leave_adjust_balance_view, name='leave-adjust-balance'),
    path('leave/calendar/', leave_calendar_view, name='leave-calendar'),
    path('leave/deferments/', leave_deferments_list_view, name='leave-deferments'),
    path('leave/deferments/apply/', leave_deferment_apply_view, name='leave-deferment-apply'),
    path('leave/deferments/<int:pk>/approve/', leave_deferment_approve_view, name='leave-deferment-approve'),
    path('leave/deferments/<int:pk>/reject/', leave_deferment_reject_view, name='leave-deferment-reject'),
    path('leave/holidays/', public_holidays_view, name='leave-holidays'),
    path('leave/holidays/add/', public_holiday_add_view, name='leave-holiday-add'),
    path('leave/holidays/<int:pk>/toggle/', public_holiday_toggle_view, name='leave-holiday-toggle'),
    path('leave/holidays/<int:pk>/delete/', public_holiday_delete_view, name='leave-holiday-delete'),

    # Leave Calculation & Entitlement APIs
    path('api/leave/calculate/', api_leave_calculate, name='api-leave-calculate'),
    path('api/leave/entitlement/', api_leave_entitlement, name='api-leave-entitlement'),
]