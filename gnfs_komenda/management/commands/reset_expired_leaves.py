from django.core.management.base import BaseCommand
from django.utils import timezone
from gnfs_komenda.models import NorminalRoll

class Command(BaseCommand):
    help = "Reset expired leaves to AT POST automatically and complete expired LeaveRequests"

    def handle(self, *args, **kwargs):
        today = timezone.localdate()
        expired = NorminalRoll.objects.filter(
            end_leave__lt=today,
            leave_status="ON LEAVE",
            called_off=False,
            manual_override=False,
        )
        count = expired.count()
        expired.update(leave_status="AT POST")

        # Also mark active LeaveRequests past their last_leave_date as COMPLETED
        from gnfs_komenda.models import LeaveRequest
        completed_reqs = LeaveRequest.objects.filter(
            last_leave_date__lt=today,
            status__in=[LeaveRequest.Status.ACTIVE, LeaveRequest.Status.APPROVED]
        ).update(status=LeaveRequest.Status.COMPLETED)

        self.stdout.write(self.style.SUCCESS(f"Reset {count} expired leaves. Completed {completed_reqs} active requests."))

