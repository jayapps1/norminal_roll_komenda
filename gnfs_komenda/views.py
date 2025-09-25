from django.shortcuts import render, get_object_or_404, redirect
from gnfs_komenda.models import NorminalRoll
from gnfs_komenda.forms import NorminalRollForm
from django.views.generic import ListView, CreateView, DeleteView, UpdateView, DetailView
from django.urls import reverse_lazy
from django.db.models import Q
import openpyxl
import pandas as pd
from django.http import HttpResponse
from django.views import View
from django.utils.timezone import now, localtime
from django.utils import timezone
from django.contrib import messages
from datetime import datetime
from django.contrib.auth.mixins import LoginRequiredMixin


# dynamic leaves officers on leave and at post count down
def officers_on_leave(request):
    today = timezone.now().date()
    
    # Officers currently on leave
    on_leave = NorminalRoll.objects.filter(
        leave_status="ON LEAVE",
        start_leave__lte=today,
        end_leave__gte=today
    )

    return render(request, "gnfs_komenda/officers_on_leave.html", {"officers": on_leave, "today": today})


def officers_at_post(request):
    today = timezone.now().date()
    
    # Officers back at post
    at_post = NorminalRoll.objects.exclude(
        id__in=NorminalRoll.objects.filter(
            leave_status="ON LEAVE",
            start_leave__lte=today,
            end_leave__gte=today
        ).values_list('id', flat=True)
    )

    return render(request, "gnfs_komenda/officers_at_post.html", {"officers": at_post})
# end dynamic leaves officers on leave and at post count down

class ExportAllPersonnelExcel(LoginRequiredMixin,View):
    def get(self, request, *args, **kwargs):
        queryset = NorminalRoll.objects.all().order_by('fullname')

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "All Personnel"

        # Fields except 'called_off'
        fields = [f for f in NorminalRoll._meta.fields if f.name != 'called_off']
        headers = [f.verbose_name.title() for f in fields]
        ws.append(headers)

        for staff in queryset:
            row = []
            for f in fields:
                value = getattr(staff, f.name)

                # Convert timezone-aware datetime to naive
                if hasattr(value, 'tzinfo') and value.tzinfo is not None:
                    value = localtime(value).replace(tzinfo=None)

                row.append(value)
            ws.append(row)

        current_date = now().strftime("%B_%Y")
        filename = f"Personnel_{current_date}.xlsx"

        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        wb.save(response)
        return response
    
class ImportPersonnelExcel(LoginRequiredMixin,View):
    template_name = "gnfs_komenda/import_personnel.html"

    def get(self, request, *args, **kwargs):
        # Show upload form
        return render(request, self.template_name)

    def post(self, request, *args, **kwargs):
        file = request.FILES.get("file")
        if not file:
            messages.error(request, "No file uploaded.")
            return redirect("import-staff")

        try:
            df = pd.read_excel(file)

            imported_count = 0
            skipped_rows = []

            for index, row in df.iterrows():
                try:
                    staffID = str(row.get("staffID")).strip()
                    if not staffID or staffID.lower() == "nan":
                        raise ValueError("Missing staffID")

                    NorminalRoll.objects.create(
                        staffID=staffID,
                        fullname=str(row.get("fullname")).strip() if pd.notna(row.get("fullname")) else "",
                        job_grade=str(row.get("job_grade")).strip() if pd.notna(row.get("job_grade")) else "",
                        birth_date=pd.to_datetime(row.get("birth_date"), errors="coerce").date()
                        if pd.notna(row.get("birth_date")) else None,
                        ghana_card=str(row.get("ghana_card")).strip() if pd.notna(row.get("ghana_card")) else None,
                        mgmt_units=str(row.get("mgmt_units")).strip() if pd.notna(row.get("mgmt_units")) else None,
                        bank=str(row.get("bank")).strip() if pd.notna(row.get("bank")) else None,
                        account_number=str(row.get("account_number")).strip() if pd.notna(row.get("account_number")) else None,
                        payroll_status=str(row.get("payroll_status")).strip() if pd.notna(row.get("payroll_status")) else None,
                        leave_type=str(row.get("leave_type")).strip() if pd.notna(row.get("leave_type")) else None,
                        leave_status=str(row.get("leave_status")).strip() if pd.notna(row.get("leave_status")) else None,
                        start_leave=pd.to_datetime(row.get("start_leave"), errors="coerce").date()
                        if pd.notna(row.get("start_leave")) else None,
                        end_leave=pd.to_datetime(row.get("end_leave"), errors="coerce").date()
                        if pd.notna(row.get("end_leave")) else None,
                        called_off=bool(row.get("called_off")) if pd.notna(row.get("called_off")) else False,
                    )
                    imported_count += 1

                except Exception as e:
                    skipped_rows.append(f"Row {index + 1} (staffID={row.get('staffID')}): {e}")

            if skipped_rows:
                messages.warning(request, f"Imported {imported_count}, Skipped {len(skipped_rows)} rows. Check console for details.")
                for error in skipped_rows:
                    print("❌", error)  # Debugging in console
            else:
                messages.success(request, f"Successfully imported {imported_count} records.")

        except Exception as e:
            messages.error(request, f"Error importing file: {e}")

        return redirect("personnel")

class AllPersonnel(LoginRequiredMixin, ListView):
    model = NorminalRoll
    template_name = 'gnfs_komenda/index.html'
    context_object_name = 'staffs'
    login_url = 'login'

    def get_queryset(self):
        queryset = super().get_queryset()
        q = self.request.GET.get("q")
        if q:
         queryset = queryset.filter(
            Q(fullname__icontains=q) | Q(staffID__icontains=q)
        )
        return queryset
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['total_staff'] = NorminalRoll.objects.count()  # count all staff
        context['staff_on_leave'] = NorminalRoll.objects.filter(leave_status='ON LEAVE').count()
        context['staff_at_post'] = NorminalRoll.objects.filter(leave_status='AT POST').count()
        return context

class AddStaff(LoginRequiredMixin, CreateView):
    model = NorminalRoll
    form_class = NorminalRollForm
    template_name = 'gnfs_komenda/create-staff.html'
    success_url = reverse_lazy('personnel')

    def form_valid(self, form):
        # Convert fields to uppercase before saving
        staff = form.save(commit=False)
        staff.staffID = staff.staffID.upper()
        staff.fullname = staff.fullname.upper()
        staff.job_grade = staff.job_grade.upper()
        staff.ghana_card = staff.ghana_card.upper()
        staff.mgmt_units = staff.mgmt_units.upper() if staff.mgmt_units else ''
        staff.bank = staff.bank.upper() if staff.bank else ''
        staff.account_number = staff.account_number.upper() if staff.account_number else ''
       
        return super().form_valid(form)

class DeleteStaff(DeleteView):
    model = NorminalRoll
    template_name = 'gnfs_komenda/index.html'
    success_url = reverse_lazy('personnel')

     # Override get_object to find by staffID instead of pk
    def get_object(self, queryset=None):
        staffID = self.kwargs.get('staffID')
        return get_object_or_404(NorminalRoll, staffID=staffID)
    
class EditStaff(UpdateView):
    model = NorminalRoll
    form_class = NorminalRollForm 
    template_name = 'gnfs_komenda/edit-staff.html'
    success_url = reverse_lazy('personnel')
    slug_field = 'staffID'         # Use staffID instead of pk
    slug_url_kwarg = 'staffID'

    def form_valid(self, form):
        staff = form.save(commit=False)

        # Convert fields to uppercase
        staff.staffID = staff.staffID.upper()
        staff.fullname = staff.fullname.upper()
        staff.job_grade = staff.job_grade.upper()
        staff.ghana_card = staff.ghana_card.upper()

        # Handle optional fields safely
        staff.mgmt_units = staff.mgmt_units.upper() if staff.mgmt_units else ''
        staff.bank = staff.bank.upper() if staff.bank else ''
        
        staff.save()
        return super().form_valid(form)
    

class StaffView(DetailView):
    model = NorminalRoll
    template_name = 'gnfs_komenda/staff-details.html'
    context_object_name = 'staff'
    slug_field = 'staffID'         # Use staffID instead of pk
    slug_url_kwarg = 'staffID'