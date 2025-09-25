from django.urls import path
from staff.views import registerstaff, stafflogin, stafflogout

urlpatterns = [
    path('register-staff/', registerstaff, name='register-staff'),
    path('login-staff/', stafflogin, name='login-staff'),
    path('logout-staff/', stafflogout, name='logout-staff'),
]