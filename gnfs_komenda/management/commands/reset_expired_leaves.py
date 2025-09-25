from django.core.management.base import BaseCommand
from django.utils import timezone
from gnfs_komenda.models import NorminalRoll

class Command(BaseCommand):
    help = "Reset expired leaves to AT POST automatically"

    def handle(self, *args, **kwargs):
        today = timezone.now().date()
        expired = NorminalRoll.objects.filter(end_leave__lt=today, called_off=False)

        count = 0
        for staff in expired:
            staff.leave_type = "AT POST"
            staff.leave_status = "AT POST"
            staff.start_leave = None
            staff.end_leave = None
            staff.save()
            count += 1

        self.stdout.write(self.style.SUCCESS(f"✅ Reset {count} expired leaves"))
