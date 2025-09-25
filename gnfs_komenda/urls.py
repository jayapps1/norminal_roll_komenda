from django.urls import path
from staff.views import stafflogin
from gnfs_komenda.views import AllPersonnel, AddStaff, DeleteStaff,EditStaff, StaffView, ExportAllPersonnelExcel, ImportPersonnelExcel,officers_on_leave, officers_at_post

urlpatterns = [
    path('',stafflogin, name='login-staff' ),
    path('personnel/', AllPersonnel.as_view(), name='personnel'),
    path('add/staff', AddStaff.as_view(), name='addstaff'),
    path('delete/staff/<str:staffID>/', DeleteStaff.as_view(), name='deletestaff'),
    path('edit/staff/<str:staffID>/', EditStaff.as_view(), name='editstaff'),
    path("staff/details/<str:staffID>/", StaffView.as_view(), name="staff-detail"),
    path('export/personnel/', ExportAllPersonnelExcel.as_view(), name='export-personnel'),
    path("import/personnel/", ImportPersonnelExcel.as_view(), name="import-personnel"),
    path('officers_on_leave/', officers_on_leave, name='officers_on_leave'),
    path('officers_at_post/', officers_at_post, name='officers_at_post'),


]