from django.urls import path
from staff.views import stafflogin
from gnfs_komenda.views import (
    AllPersonnel, AddStaff, DeleteStaff, EditStaff, StaffView,
    ExportAllPersonnelExcel, ImportPersonnelExcel,
    officers_on_leave, officers_at_post,
    NationalDashboardView, RegionalDashboardView, DistrictDashboardView,
    StationDashboardView, PostDashboardView,
    RegionalCommandDashboardView, DistrictCommandDashboardView,
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
    leave_balances_view, leave_adjust_balance_view, leave_set_proportionate_entitlement_view, leave_calendar_view,
    leave_deferments_list_view, leave_deferment_apply_view,
    leave_deferment_approve_view, leave_deferment_reject_view,
    public_holidays_view, public_holiday_add_view,
    public_holiday_toggle_view, public_holiday_delete_view,
    leave_policies_list_view,
    api_directorates, api_functional_children, switch_workspace_view,
    FunctionalDashboardView, transfer_list_view, transfer_create_view,
    transfer_detail_view, transfer_approve_view, transfer_release_view,
    transfer_mark_reported_view, transfer_complete_placement_view,
    transfer_confirm_arrival_view, transfer_cancel_view,
    region_management_view, manage_regional_leadership_view,
    manage_district_leadership_view, organisation_tree_view,
    directorate_list_view, directorate_create_view, directorate_update_view, directorate_toggle_view,
    department_list_view, department_create_view, department_update_view, department_toggle_view,
    unit_list_view, unit_create_view, unit_update_view, unit_toggle_view,
    personnel_add_functional_assignment_view,
    personnel_remove_functional_assignment_view,
    personnel_set_primary_assignment_view
)
from gnfs_komenda.self_service_views import (
    my_dashboard_view, my_leave_view, my_leave_request_view,
    my_leave_detail_view, my_leave_edit_view, my_profile_view,
    my_posting_view, my_departments_view, my_transfers_view,
    my_notifications_view, my_profile_picture_upload_view,
    personnel_profile_picture_upload_view
)
from gnfs_komenda.workflow_inbox_views import (
    district_leave_inbox_view, district_leave_process_view,
    district_leave_approvals_view, district_leave_decision_view,
    regional_leave_inbox_view, regional_leave_process_view,
    regional_leave_approvals_view, regional_leave_decision_view
)
from gnfs_komenda.role_views import (
    role_list_view, assign_administrative_role_view,
    revoke_administrative_role_view, role_permissions_edit_view,
    api_organisational_cascade_view, api_roles_personnel_search_view
)
from gnfs_komenda.functional_views import (
    workspace_accounts_district_view, workspace_accounts_regional_view, workspace_accounts_national_view,
    workspace_admin_district_view, workspace_admin_regional_view, workspace_admin_national_view,
    admin_create_leave_view,
    workspace_ops_district_view, workspace_ops_regional_view, workspace_ops_national_view,
    workspace_hr_district_view,
    workspace_watch_room_district_view,
    admin_process_leave_action, operations_recommend_leave_action,
    command_approve_leave_action, admin_release_to_watch_room_action,
    watch_room_book_leave_action, watch_room_record_resumption_action
)

urlpatterns = [
    # Core Roster & Auth Entrance
    path('', stafflogin, name='login-staff'),
    path('personnel/', AllPersonnel.as_view(), name='personnel'),
    path('add/staff', AddStaff.as_view(), name='addstaff'),
    path('delete/staff/<path:staffID>/', DeleteStaff.as_view(), name='deletestaff'),
    path('edit/staff/<path:staffID>/', EditStaff.as_view(), name='editstaff'),
    path("staff/details/<path:staffID>/", StaffView.as_view(), name="staff-detail"),
    path('export/personnel/', ExportAllPersonnelExcel.as_view(), name='export-personnel'),
    path("import/personnel/", ImportPersonnelExcel.as_view(), name="import-personnel"),
    path('officers_on_leave/', officers_on_leave, name='officers_on_leave'),
    path('officers_at_post/', officers_at_post, name='officers_at_post'),

    # Hierarchical Dashboards (Administration & Command Separated)
    path('dashboard/national/', NationalDashboardView.as_view(), name='dashboard-national'),
    path('dashboard/region/<int:region_id>/', RegionalDashboardView.as_view(), name='dashboard-regional'),
    path('dashboard/regional/<int:region_id>/', RegionalDashboardView.as_view()),
    path('dashboard/region/<int:region_id>/command/', RegionalCommandDashboardView.as_view(), name='dashboard-regional-command'),
    path('dashboard/regional/<int:region_id>/command/', RegionalCommandDashboardView.as_view()),
    path('dashboard/district/<int:district_id>/', DistrictDashboardView.as_view(), name='dashboard-district'),
    path('dashboard/district/<int:district_id>/command/', DistrictCommandDashboardView.as_view(), name='dashboard-district-command'),
    path('dashboard/station/<int:station_id>/', StationDashboardView.as_view(), name='dashboard-station'),
    path('dashboard/post/<int:post_id>/', PostDashboardView.as_view(), name='dashboard-post'),

    # Role & Administrative Assignment Management (Section 8-18, 41-45)
    path('roles/', role_list_view, name='role-list'),
    path('roles/assign/', assign_administrative_role_view, name='assign-role'),
    path('roles/revoke/<int:assignment_id>/', revoke_administrative_role_view, name='revoke-role'),
    path('roles/<int:role_id>/permissions/', role_permissions_edit_view, name='role-permissions-edit'),
    path('roles/api/cascade/', api_organisational_cascade_view, name='roles-api-cascade'),
    path('roles/api/personnel-search/', api_roles_personnel_search_view, name='roles-personnel-search'),
    path('ajax/personnel-search/', api_roles_personnel_search_view, name='ajax-personnel-search'),

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
    path('leave/balances/<int:entitlement_id>/prorate/', leave_set_proportionate_entitlement_view, name='leave-set-proportionate'),
    path('leave/balances/<int:entitlement_id>/proration/', leave_set_proportionate_entitlement_view, name='leave-set-proportionate-entitlement'),
    path('leave/calendar/', leave_calendar_view, name='leave-calendar'),
    path('leave/deferments/', leave_deferments_list_view, name='leave-deferments'),
    path('leave/deferments/apply/', leave_deferment_apply_view, name='leave-deferment-apply'),
    path('leave/deferments/<int:pk>/approve/', leave_deferment_approve_view, name='leave-deferment-approve'),
    path('leave/deferments/<int:pk>/reject/', leave_deferment_reject_view, name='leave-deferment-reject'),
    path('leave/holidays/', public_holidays_view, name='leave-holidays'),
    path('leave/holidays/add/', public_holiday_add_view, name='leave-holiday-add'),
    path('leave/holidays/<int:pk>/toggle/', public_holiday_toggle_view, name='leave-holiday-toggle'),
    path('leave/holidays/<int:pk>/delete/', public_holiday_delete_view, name='leave-holiday-delete'),
    path('leave/policies/', leave_policies_list_view, name='leave-policies'),

    # Leave Calculation & Entitlement APIs
    path('api/leave/calculate/', api_leave_calculate, name='api-leave-calculate'),
    path('api/leave/entitlement/', api_leave_entitlement, name='api-leave-entitlement'),

    # Functional Cascading APIs & Workspace Switcher
    path('api/directorates/', api_directorates, name='api-directorates'),
    path('api/functional-children/', api_functional_children, name='api-functional-children'),
    path('workspace/switch/<str:workspace_id>/', switch_workspace_view, name='switch-workspace'),
    path('dashboard/functional/<int:unit_id>/', FunctionalDashboardView.as_view(), name='dashboard-functional'),

    # Personnel Transfer & Posting Management
    path('transfers/', transfer_list_view, name='transfer-list'),
    path('transfers/create/<int:personnel_id>/', transfer_create_view, name='transfer-create'),
    path('transfers/<int:transfer_id>/', transfer_detail_view, name='transfer-detail'),
    path('transfers/<int:transfer_id>/approve/', transfer_approve_view, name='transfer-approve'),
    path('transfers/<int:transfer_id>/release/', transfer_release_view, name='transfer-release'),
    path('transfers/<int:transfer_id>/report/', transfer_mark_reported_view, name='transfer-report'),
    path('transfers/<int:transfer_id>/placement/', transfer_complete_placement_view, name='transfer-placement'),
    path('transfers/<int:transfer_id>/confirm/', transfer_confirm_arrival_view, name='transfer-confirm'),
    path('transfers/<int:transfer_id>/cancel/', transfer_cancel_view, name='transfer-cancel'),

    # Command Leadership Management (Section 17)
    path('organisation/region/<int:region_id>/', region_management_view, name='region-management'),
    path('organisation/region/<int:region_id>/leadership/', manage_regional_leadership_view, name='manage-region-leadership'),
    path('organisation/district/<int:district_id>/leadership/', manage_district_leadership_view, name='manage-district-leadership'),

    # Functional Organisational Structure & Tree (Section 17)
    path('organisation/tree/', organisation_tree_view, name='organisation-tree'),
    path('organisation/directorates/', directorate_list_view, name='directorates-list'),
    path('organisation/directorates/add/', directorate_create_view, name='directorate-create'),
    path('organisation/directorates/<int:pk>/edit/', directorate_update_view, name='directorate-edit'),
    path('organisation/directorates/<int:pk>/toggle/', directorate_toggle_view, name='directorate-toggle'),
    path('organisation/departments/', department_list_view, name='departments-list'),
    path('organisation/departments/add/', department_create_view, name='department-create'),
    path('organisation/departments/<int:pk>/edit/', department_update_view, name='department-edit'),
    path('organisation/departments/<int:pk>/toggle/', department_toggle_view, name='department-toggle'),
    path('organisation/units/', unit_list_view, name='units-list'),
    path('organisation/units/add/', unit_create_view, name='unit-create'),
    path('organisation/units/<int:pk>/edit/', unit_update_view, name='unit-edit'),
    path('organisation/units/<int:pk>/toggle/', unit_toggle_view, name='unit-toggle'),

    # Personnel Multiple Functional Assignments (Section 17)
    path('personnel/<int:personnel_id>/functional-assignment/add/', personnel_add_functional_assignment_view, name='personnel-add-functional-assignment'),
    path('personnel/functional-assignment/<int:assignment_id>/remove/', personnel_remove_functional_assignment_view, name='personnel-remove-functional-assignment'),
    path('personnel/functional-assignment/<int:assignment_id>/set-primary/', personnel_set_primary_assignment_view, name='personnel-set-primary-assignment'),

    # Personnel Self-Service Portal (Section 1-10, 32-36)
    path('my/dashboard/', my_dashboard_view, name='my-dashboard'),
    path('my/leave/', my_leave_view, name='my-leave'),
    path('my/leave/request/', my_leave_request_view, name='my-leave-request'),
    path('my/leave/<int:pk>/', my_leave_detail_view, name='my-leave-detail'),
    path('my/leave/<int:pk>/edit/', my_leave_edit_view, name='my-leave-edit'),
    path('my/profile/', my_profile_view, name='my-profile'),
    path('my/posting/', my_posting_view, name='my-posting'),
    path('my/departments/', my_departments_view, name='my-departments'),
    path('my/transfers/', my_transfers_view, name='my-transfers'),
    path('my/notifications/', my_notifications_view, name='my-notifications'),
    path('my/profile/picture/', my_profile_picture_upload_view, name='my-profile-picture-upload'),
    path('personnel/<int:personnel_id>/profile-picture/', personnel_profile_picture_upload_view, name='personnel-profile-picture-upload'),

    # District Administration & Command Workflow Inboxes (Section 11-15)
    path('district/<int:district_id>/leave/inbox/', district_leave_inbox_view, name='district-leave-inbox'),
    path('district/<int:district_id>/leave/<int:pk>/process/', district_leave_process_view, name='district-leave-process'),
    path('district/<int:district_id>/leave/approvals/', district_leave_approvals_view, name='district-leave-approvals'),
    path('district/<int:district_id>/leave/<int:pk>/decision/', district_leave_decision_view, name='district-leave-decision'),

    # Regional Administration & Command Workflow Inboxes (Section 16-21)
    path('regional/<int:region_id>/leave/inbox/', regional_leave_inbox_view, name='regional-leave-inbox'),
    path('regional/<int:region_id>/leave/<int:pk>/process/', regional_leave_process_view, name='regional-leave-process'),
    path('regional/<int:region_id>/leave/approvals/', regional_leave_approvals_view, name='regional-leave-approvals'),
    path('regional/<int:region_id>/leave/<int:pk>/decision/', regional_leave_decision_view, name='regional-leave-decision'),

    # Functional Workspaces (Accounts / Nominal Roll, Administration, Operations, HR, Watch Room)
    path('workspace/accounts/district/<int:district_id>/', workspace_accounts_district_view, name='workspace-accounts-district'),
    path('workspace/accounts/region/<int:region_id>/', workspace_accounts_regional_view, name='workspace-accounts-regional'),
    path('workspace/accounts/national/', workspace_accounts_national_view, name='workspace-accounts-national'),

    path('workspace/admin/district/<int:district_id>/', workspace_admin_district_view, name='workspace-admin-district'),
    path('workspace/admin/district/<int:district_id>/create-leave/', admin_create_leave_view, name='admin-create-leave'),
    path('workspace/admin/region/<int:region_id>/', workspace_admin_regional_view, name='workspace-admin-regional'),
    path('workspace/admin/national/', workspace_admin_national_view, name='workspace-admin-national'),

    path('workspace/ops/district/<int:district_id>/', workspace_ops_district_view, name='workspace-ops-district'),
    path('workspace/ops/region/<int:region_id>/', workspace_ops_regional_view, name='workspace-ops-regional'),
    path('workspace/ops/national/', workspace_ops_national_view, name='workspace-ops-national'),

    path('workspace/hr/district/<int:district_id>/', workspace_hr_district_view, name='workspace-hr-district'),

    path('workspace/watch-room/district/<int:district_id>/', workspace_watch_room_district_view, name='workspace-watch-room-district'),

    # Multi-Stage Workflow Actions (Admin Verification, Operations Recommendation, Command Approval, Watch Room Booking)
    path('workflow/leave/<int:leave_req_id>/admin-process/', admin_process_leave_action, name='admin-process-leave'),
    path('workflow/leave/<int:leave_req_id>/operations-recommend/', operations_recommend_leave_action, name='operations-recommend-leave'),
    path('workflow/leave/<int:leave_req_id>/command-approve/', command_approve_leave_action, name='command-approve-leave'),
    path('workflow/leave/<int:leave_req_id>/admin-release-watch-room/', admin_release_to_watch_room_action, name='admin-release-watch-room'),
    path('workflow/leave/<int:leave_req_id>/watch-room-book/', watch_room_book_leave_action, name='watch-room-book-leave'),
    path('workflow/leave/<int:leave_req_id>/watch-room-resume/', watch_room_record_resumption_action, name='watch-room-resume'),
]