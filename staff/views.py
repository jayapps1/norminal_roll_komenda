from django.shortcuts import render, redirect
from staff.forms import RegisterStaffForm
from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.contrib import messages

def registerstaff(request):
    if request.method == 'POST':
        form = RegisterStaffForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)  # login only once
            messages.success(request, "Staff added and logged in successfully.")
            return redirect('personnel')
        else:
            messages.error(request, "Kindly correct the errors.")
    else:
        form = RegisterStaffForm()
    return render(request, 'staff/register-staff.html', {'form': form})


def stafflogin(request):
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()  # ✅ cleaner
            login(request, user)
            messages.success(request, "Login successful!")
            return redirect("personnel")
        else:
            messages.error(request, "Invalid username or password.")
    else:
        form = AuthenticationForm()
    return render(request, "staff/staff-login.html", {"form": form})


def stafflogout(request):
    logout(request)
    messages.success(request, "You have been logged out.")
    return redirect('login-staff')
