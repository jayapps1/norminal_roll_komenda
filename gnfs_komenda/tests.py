from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from gnfs_komenda.forms import NorminalRollForm, LeaveRequestForm
from gnfs_komenda.models import (
    ApprovalHistory, AuditLog, District, Notification, NorminalRoll,
    PersonnelActionRequest, Post, Rank, Region, Station
)
from gnfs_komenda.security import (
    can_user_approve_request, get_personnel_queryset_for_user
)
from gnfs_komenda.workflow import (
    create_archive_request, process_approval_decision
)
from staff.models import GNFSUserRole, UserAssignment

User = get_user_model()


# ==============================================================================
# EXISTING LEAVE TESTS (PRESERVED)
# ==============================================================================

class LeaveStatusTests(TestCase):
    def setUp(self):
        self.today = timezone.now().date()

    def test_leave_status_is_on_leave_when_within_range(self):
        staff = NorminalRoll.objects.create(
            staffID="10001",
            fullname="TEST OFFICER",
            job_grade="STATION OFFICER",
            birth_date=self.today - timedelta(days=365 * 30),
            ghana_card="GHA-123456789-1",
            mgmt_units="KOMENDA",
            bank="GCB",
            account_number="1234567890",
            payroll_status="ACTIVE",
            start_leave=self.today - timedelta(days=2),
            end_leave=self.today + timedelta(days=5),
        )
        self.assertEqual(staff.leave_status, "ON LEAVE")

    def test_leave_status_is_at_post_when_leave_exhausted(self):
        staff = NorminalRoll.objects.create(
            staffID="10002",
            fullname="EXHAUSTED OFFICER",
            job_grade="FIREMAN",
            birth_date=self.today - timedelta(days=365 * 25),
            ghana_card="GHA-123456789-2",
            mgmt_units="KOMENDA",
            bank="GCB",
            account_number="1234567891",
            payroll_status="ACTIVE",
            start_leave=self.today - timedelta(days=20),
            end_leave=self.today - timedelta(days=1),
        )
        self.assertEqual(staff.leave_status, "AT POST")

    def test_sync_leaves_and_reset_command_reset_expired_leaves(self):
        staff = NorminalRoll.objects.create(
            staffID="10003",
            fullname="OLD LEAVE OFFICER",
            job_grade="FIREMAN",
            birth_date=self.today - timedelta(days=365 * 25),
            ghana_card="GHA-123456789-3",
            mgmt_units="KOMENDA",
            bank="GCB",
            account_number="1234567892",
            payroll_status="ACTIVE",
            manual_override=True,
            leave_status="ON LEAVE",
            start_leave=self.today - timedelta(days=20),
            end_leave=self.today - timedelta(days=1),
        )
        NorminalRoll.objects.filter(id=staff.id).update(manual_override=False)

        call_command("reset_expired_leaves")

        staff.refresh_from_db()
        self.assertEqual(staff.leave_status, "AT POST")


# ==============================================================================
# ORGANISATIONAL STRUCTURE TESTS
# ==============================================================================

class OrganisationalStructureTests(TestCase):
    def test_hierarchy_creation_and_integrity(self):
        region = Region.objects.create(name="Central Region", code="CR")
        district = District.objects.create(region=region, name="KEEA Municipal", code="CR-KEEA")
        station = Station.objects.create(district=district, name="Komenda Fire Station", code="CR-KOM", station_type="DISTRICT")
        post = Post.objects.create(station=station, name="Komenda Market Post", code="CR-KOM-P1", post_type="MARKET_POST")

        self.assertEqual(district.region, region)
        self.assertEqual(station.district, district)
        self.assertEqual(post.station, station)

        # Unique constraint checks
        with self.assertRaises(IntegrityError):
            District.objects.create(region=region, name="KEEA Municipal")

    def test_seeder_idempotency_and_safe_personnel_link(self):
        today = timezone.now().date()
        unlinked_staff = NorminalRoll.objects.create(
            staffID="55501",
            fullname="KOMENDA OFFICER",
            job_grade="FIREMAN",
            birth_date=today - timedelta(days=365 * 25),
            ghana_card="GHA-555000111-1",
            mgmt_units="0307 GNFS, KEEA",
            bank="GCB",
            account_number="111222333",
            payroll_status="ACTIVE",
            station=None
        )

        # Run seeder first time
        call_command("seed_gnfs_structure")
        unlinked_staff.refresh_from_db()
        self.assertIsNotNone(unlinked_staff.station)
        self.assertEqual(unlinked_staff.station.name, "Komenda Fire Station")

        reg_count1 = Region.objects.count()
        dist_count1 = District.objects.count()
        stn_count1 = Station.objects.count()

        # Verify all 17 GNFS Service Administrative Regions are seeded (including Tema Region)
        self.assertEqual(reg_count1, 17)
        self.assertTrue(Region.objects.filter(code="TR", name="Tema Region").exists())
        self.assertTrue(Region.objects.filter(code="CR", name="Central Region").exists())

        # Run seeder second time (Idempotency verification)
        call_command("seed_gnfs_structure")
        self.assertEqual(Region.objects.count(), reg_count1)
        self.assertEqual(District.objects.count(), dist_count1)
        self.assertEqual(Station.objects.count(), stn_count1)


# ==============================================================================
# DATA-SCOPE SECURITY & PERMISSION TESTS
# ==============================================================================

class DataScopeSecurityTests(TestCase):
    def setUp(self):
        self.today = timezone.now().date()

        # Create Hierarchy
        self.region_central = Region.objects.create(name="Central Region", code="CR")
        self.region_accra = Region.objects.create(name="Greater Accra Region", code="GAR")

        self.dist_keea = District.objects.create(region=self.region_central, name="KEEA Municipal", code="CR-KEEA")
        self.dist_ama = District.objects.create(region=self.region_accra, name="Accra Metropolis", code="GAR-AMA")

        self.stn_komenda = Station.objects.create(district=self.dist_keea, name="Komenda Fire Station", code="CR-KOM")
        self.stn_makola = Station.objects.create(district=self.dist_ama, name="Makola Fire Station", code="GAR-MAK")

        self.post_komenda = Post.objects.create(station=self.stn_komenda, name="Komenda Market Post", code="CR-KP1")

        # Create Personnel
        self.staff_komenda = NorminalRoll.objects.create(
            staffID="80001",
            fullname="KWAME KOMENDA",
            job_grade="DOIII",
            birth_date=self.today - timedelta(days=365 * 30),
            ghana_card="GHA-800010001-1",
            station=self.stn_komenda,
            post=self.post_komenda,
            bank="GCB",
            account_number="888001"
        )
        self.staff_makola = NorminalRoll.objects.create(
            staffID="80002",
            fullname="KOFI MAKOLA",
            job_grade="ADOI",
            birth_date=self.today - timedelta(days=365 * 32),
            ghana_card="GHA-800020002-2",
            station=self.stn_makola,
            bank="ADB",
            account_number="888002"
        )

        # Users and Roles
        self.u_national = User.objects.create_user(username="nat_admin", password="password123")
        UserAssignment.objects.create(user=self.u_national, role=GNFSUserRole.NATIONAL_ADMIN)

        self.u_central = User.objects.create_user(username="cr_admin", password="password123")
        UserAssignment.objects.create(user=self.u_central, role=GNFSUserRole.REGIONAL_ADMIN, region=self.region_central)

        self.u_dfo = User.objects.create_user(username="keea_dfo", password="password123")
        UserAssignment.objects.create(user=self.u_dfo, role=GNFSUserRole.DFO, district=self.dist_keea)

        self.u_stn = User.objects.create_user(username="kom_stn_admin", password="password123")
        UserAssignment.objects.create(user=self.u_stn, role=GNFSUserRole.STATION_ADMIN, station=self.stn_komenda)

    def test_national_admin_nationwide_queryset(self):
        qs = get_personnel_queryset_for_user(self.u_national)
        self.assertIn(self.staff_komenda, qs)
        self.assertIn(self.staff_makola, qs)

    def test_regional_admin_data_isolation(self):
        qs = get_personnel_queryset_for_user(self.u_central)
        self.assertIn(self.staff_komenda, qs)
        self.assertNotIn(self.staff_makola, qs)

    def test_dfo_and_station_admin_data_isolation(self):
        qs_dfo = get_personnel_queryset_for_user(self.u_dfo)
        self.assertIn(self.staff_komenda, qs_dfo)
        self.assertNotIn(self.staff_makola, qs_dfo)

        qs_stn = get_personnel_queryset_for_user(self.u_stn)
        self.assertIn(self.staff_komenda, qs_stn)
        self.assertNotIn(self.staff_makola, qs_stn)

    def test_idor_protection_on_detail_view(self):
        self.client.force_login(self.u_central)
        # Attempt to access Makola personnel detail (belongs to Greater Accra)
        response = self.client.get(reverse("staff-detail", kwargs={"staffID": self.staff_makola.staffID}))
        self.assertEqual(response.status_code, 403)

        # Access authorized Komenda personnel
        response_ok = self.client.get(reverse("staff-detail", kwargs={"staffID": self.staff_komenda.staffID}))
        self.assertEqual(response_ok.status_code, 200)

    def test_live_search_scoped_results(self):
        self.client.force_login(self.u_central)
        response = self.client.get(reverse("api-personnel-search") + "?q=KOFI")
        data = response.json()
        # Should not find KOFI MAKOLA because he is outside Central Region
        self.assertEqual(data["count"], 0)

        response_kom = self.client.get(reverse("api-personnel-search") + "?q=KWAME")
        data_kom = response_kom.json()
        self.assertEqual(data_kom["count"], 1)
        self.assertEqual(data_kom["results"][0]["staffID"], self.staff_komenda.staffID)


# ==============================================================================
# APPROVAL WORKFLOW TESTS
# ==============================================================================

class ApprovalWorkflowTests(TestCase):
    def setUp(self):
        self.today = timezone.now().date()
        self.region = Region.objects.create(name="Central Region", code="CR")
        self.district = District.objects.create(region=self.region, name="KEEA Municipal", code="CR-KEEA")
        self.station = Station.objects.create(district=self.district, name="Komenda Fire Station", code="CR-KOM")

        self.staff = NorminalRoll.objects.create(
            staffID="90001",
            fullname="ARCHIVE TARGET",
            job_grade="FIREMAN",
            birth_date=self.today - timedelta(days=365 * 25),
            ghana_card="GHA-900010001-1",
            station=self.station,
            bank="GCB",
            account_number="999001"
        )

        self.u_requester = User.objects.create_user(username="station_officer", password="password123")
        UserAssignment.objects.create(user=self.u_requester, role=GNFSUserRole.STATION_ADMIN, station=self.station)

        self.u_dfo = User.objects.create_user(username="dfo_officer", password="password123")
        UserAssignment.objects.create(user=self.u_dfo, role=GNFSUserRole.DFO, district=self.district)

        self.u_reg = User.objects.create_user(username="reg_officer", password="password123")
        UserAssignment.objects.create(user=self.u_reg, role=GNFSUserRole.REGIONAL_ADMIN, region=self.region)

        self.u_nat = User.objects.create_user(username="nat_officer", password="password123")
        UserAssignment.objects.create(user=self.u_nat, role=GNFSUserRole.NATIONAL_ADMIN)

    def test_full_approval_chain_dfo_to_regional_to_national_and_soft_archival(self):
        # 1. Create Request
        req = create_archive_request(
            personnel=self.staff,
            requester=self.u_requester,
            reason="Retirement from GNFS service"
        )
        self.assertEqual(req.status, "PENDING_DFO")
        self.assertTrue(self.staff.is_active)

        # 2. DFO Approves -> Transitions to PENDING_REGIONAL
        process_approval_decision(req, self.u_dfo, "APPROVED", comment="DFO verified.")
        req.refresh_from_db()
        self.assertEqual(req.status, "PENDING_REGIONAL")
        self.assertEqual(req.current_approval_level, "REGIONAL")
        self.assertTrue(self.staff.is_active)

        # 3. Regional Admin Approves -> Transitions to PENDING_NATIONAL
        process_approval_decision(req, self.u_reg, "APPROVED", comment="Regional HQ recommended.")
        req.refresh_from_db()
        self.assertEqual(req.status, "PENDING_NATIONAL")
        self.assertEqual(req.current_approval_level, "NATIONAL")
        self.assertTrue(self.staff.is_active)

        # 4. National Admin Approves -> Final Archival Execution
        process_approval_decision(req, self.u_nat, "APPROVED", comment="Final national approval granted.")
        req.refresh_from_db()
        self.staff.refresh_from_db()

        self.assertEqual(req.status, "APPROVED")
        self.assertFalse(self.staff.is_active)
        self.assertEqual(self.staff.archived_by, self.u_nat)
        self.assertIn("Retirement", self.staff.archive_reason)

        # Verify Audit Log was recorded
        self.assertTrue(AuditLog.objects.filter(action="NATIONAL_APPROVED").exists())

    def test_rejection_at_dfo_level_stops_workflow(self):
        req = create_archive_request(
            personnel=self.staff,
            requester=self.u_requester,
            reason="Unsubstantiated archival"
        )
        process_approval_decision(req, self.u_dfo, "REJECTED", comment="DFO rejected: Officer has pending service inquiry.")
        req.refresh_from_db()
        self.staff.refresh_from_db()

        self.assertEqual(req.status, "REJECTED")
        self.assertTrue(self.staff.is_active)  # Personnel remains active!

    def test_separation_of_duties_requester_cannot_approve_own_request(self):
        req = create_archive_request(
            personnel=self.staff,
            requester=self.u_dfo,
            reason="DFO initiates archival"
        )
        self.assertFalse(can_user_approve_request(self.u_dfo, req))
        with self.assertRaises(PermissionDenied):
            process_approval_decision(req, self.u_dfo, "APPROVED")


# ==============================================================================
# STRUCTURE MANAGEMENT PORTAL & LIVE SEARCH TESTS
# ==============================================================================

class StructureManagementPortalTests(TestCase):
    def setUp(self):
        self.client = Client()

        # National Admin
        self.admin_user = User.objects.create_superuser("nat_admin", "nat@gnfs.gov.gh", "pass1234")

        # Regular Staff
        self.regular_user = User.objects.create_user("reg_staff", "staff@gnfs.gov.gh", "pass1234")

        # Sample hierarchy
        self.region = Region.objects.create(name="Volta Region", code="VR")
        self.district = District.objects.create(region=self.region, name="Ho Municipal", code="VR-HO")
        self.station = Station.objects.create(district=self.district, name="Ho Central Fire Station", station_type="MUNICIPAL")
        self.post = Post.objects.create(station=self.station, name="Ho Central Market Post", post_type="MARKET_POST")

    def test_unauthenticated_and_regular_staff_access_denied(self):
        # Anonymous user redirected to login
        res = self.client.get(reverse("structure-manage"))
        self.assertEqual(res.status_code, 302)
        self.assertIn("login", res.url)

        # Regular staff redirected away with error message
        self.client.force_login(self.regular_user)
        res = self.client.get(reverse("structure-manage"))
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.url, reverse("personnel"))

    def test_national_admin_access_granted(self):
        self.client.force_login(self.admin_user)
        res = self.client.get(reverse("structure-manage"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "GNFS Organisational Structure Directory")
        self.assertContains(res, "Volta Region")
        self.assertContains(res, "Ho Municipal")
        self.assertContains(res, "Ho Central Fire Station")

    def test_add_and_edit_district(self):
        self.client.force_login(self.admin_user)

        # Add district
        add_res = self.client.post(reverse("add-district"), {
            "region": self.region.id,
            "name": "Keta Municipal",
            "code": "VR-KTA",
            "active": True
        })
        self.assertEqual(add_res.status_code, 302)
        new_dist = District.objects.filter(name="Keta Municipal").first()
        self.assertIsNotNone(new_dist)
        self.assertEqual(new_dist.code, "VR-KTA")

        # Edit district
        edit_res = self.client.post(reverse("edit-district", args=[new_dist.id]), {
            "region": self.region.id,
            "name": "Keta Municipal Command",
            "code": "VR-KETA",
            "active": True
        })
        self.assertEqual(edit_res.status_code, 302)
        new_dist.refresh_from_db()
        self.assertEqual(new_dist.name, "Keta Municipal Command")
        self.assertEqual(new_dist.code, "VR-KETA")

    def test_add_and_edit_station(self):
        self.client.force_login(self.admin_user)

        # Add station
        add_res = self.client.post(reverse("add-station"), {
            "district": self.district.id,
            "name": "Sokode Fire Station",
            "code": "VR-SOK",
            "station_type": "DISTRICT",
            "active": True
        })
        self.assertEqual(add_res.status_code, 302)
        stn = Station.objects.filter(name="Sokode Fire Station").first()
        self.assertIsNotNone(stn)
        self.assertEqual(stn.station_type, "DISTRICT")

        # Edit station
        edit_res = self.client.post(reverse("edit-station", args=[stn.id]), {
            "district": self.district.id,
            "name": "Sokode Fire Station",
            "code": "VR-SOK-01",
            "station_type": "SUB_STATION",
            "active": True
        })
        self.assertEqual(edit_res.status_code, 302)
        stn.refresh_from_db()
        self.assertEqual(stn.station_type, "SUB_STATION")
        self.assertEqual(stn.code, "VR-SOK-01")

    def test_toggle_unit_active(self):
        self.client.force_login(self.admin_user)

        self.assertTrue(self.station.active)
        # Toggle to Inactive
        res = self.client.post(reverse("toggle-unit-active", args=["station", self.station.id]))
        self.assertEqual(res.status_code, 302)
        self.station.refresh_from_db()
        self.assertFalse(self.station.active)

        # Toggle back to Active
        res = self.client.post(reverse("toggle-unit-active", args=["station", self.station.id]))
        self.assertEqual(res.status_code, 302)
        self.station.refresh_from_db()
        self.assertTrue(self.station.active)

    def test_safe_delete_blocks_when_children_exist(self):
        self.client.force_login(self.admin_user)

        # Attempt to delete region that contains districts
        dist_count_before = District.objects.count()
        reg_count_before = Region.objects.count()

        res = self.client.post(reverse("delete-unit", args=["region", self.region.id]))
        self.assertEqual(res.status_code, 302)

        # Region should NOT have been deleted
        self.assertEqual(Region.objects.count(), reg_count_before)
        self.assertTrue(Region.objects.filter(id=self.region.id).exists())

    def test_safe_delete_succeeds_when_no_children(self):
        self.client.force_login(self.admin_user)

        empty_post = Post.objects.create(station=self.station, name="Empty Test Post", post_type="COMMUNITY_POST")
        post_id = empty_post.id

        res = self.client.post(reverse("delete-unit", args=["post", post_id]))
        self.assertEqual(res.status_code, 302)
        self.assertFalse(Post.objects.filter(id=post_id).exists())

    def test_api_structure_search(self):
        self.client.force_login(self.admin_user)

        res = self.client.get(reverse("api-structure-search"), {"q": "Volta"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertGreaterEqual(data["counts"]["regions"], 1)
        self.assertEqual(data["regions"][0]["code"], "VR")


# ==============================================================================
# GNFS RANK SEEDER & DYNAMIC PERSONNEL FORM TESTS
# ==============================================================================

class GNFSRankAndDynamicPersonnelTests(TestCase):
    def setUp(self):
        self.today = timezone.now().date()
        # Seed ranks
        call_command("seed_gnfs_ranks")

        # Create organizational structure
        self.region_cr = Region.objects.create(name="Central Region", code="CR")
        self.dist_keea = District.objects.create(region=self.region_cr, name="KEEA Municipal", code="CR-KEEA")
        self.stn_komenda = Station.objects.create(district=self.dist_keea, name="Komenda Fire Station", code="CR-KOM")

        self.region_ar = Region.objects.create(name="Ashanti Region", code="AR")
        self.dist_kma = District.objects.create(region=self.region_ar, name="Kumasi Metro", code="AR-KMA")
        self.stn_kumasi = Station.objects.create(district=self.dist_kma, name="Kumasi Central Fire Station", code="AR-KMA-01")

        # Admin and Station Users
        self.national_admin = User.objects.create_superuser("natadmin", "nat@example.com", "pass123")
        UserAssignment.objects.create(user=self.national_admin, role=GNFSUserRole.NATIONAL_ADMIN, active=True)

        self.station_admin = User.objects.create_user("stnadmin", "stn@example.com", "pass123")
        UserAssignment.objects.create(
            user=self.station_admin,
            role=GNFSUserRole.STATION_ADMIN,
            region=self.region_cr,
            district=self.dist_keea,
            station=self.stn_komenda,
            active=True
        )

        self.client = Client()

    def test_official_ranks_seeded_properly(self):
        """Verify 9 Senior Officer ranks and 8 Junior Officer ranks are seeded with correct ordering."""
        senior_ranks = Rank.objects.filter(officer_category=Rank.OfficerCategory.SENIOR)
        junior_ranks = Rank.objects.filter(officer_category=Rank.OfficerCategory.JUNIOR)

        self.assertEqual(senior_ranks.count(), 9)
        self.assertEqual(junior_ranks.count(), 8)
        self.assertEqual(Rank.objects.count(), 17)

        # CFO hierarchy order should be 1
        cfo = Rank.objects.get(abbreviation="CFO")
        self.assertEqual(cfo.hierarchy_order, 1)
        self.assertEqual(cfo.officer_category, Rank.OfficerCategory.SENIOR)

        # STN/OI / GO I should be hierarchy order 10
        stnoi = Rank.objects.get(abbreviation="STN/OI / GO I")
        self.assertEqual(stnoi.hierarchy_order, 10)
        self.assertEqual(stnoi.officer_category, Rank.OfficerCategory.JUNIOR)

        # RFM / RFW hierarchy order should be 17
        rfm = Rank.objects.get(abbreviation="RFM / RFW")
        self.assertEqual(rfm.hierarchy_order, 17)
        self.assertEqual(rfm.officer_category, Rank.OfficerCategory.JUNIOR)

    def test_seeder_is_idempotent(self):
        """Running seed_gnfs_ranks multiple times does not duplicate or alter ranks."""
        call_command("seed_gnfs_ranks")
        call_command("seed_gnfs_ranks")
        self.assertEqual(Rank.objects.count(), 17)
        self.assertEqual(Rank.objects.filter(officer_category=Rank.OfficerCategory.SENIOR).count(), 9)
        self.assertEqual(Rank.objects.filter(officer_category=Rank.OfficerCategory.JUNIOR).count(), 8)

    def test_api_ranks_returns_filtered_by_category(self):
        """Dynamic API returns only senior or junior ranks based on category parameter."""
        self.client.force_login(self.national_admin)

        # Test Senior
        res_senior = self.client.get(reverse("api-ranks") + "?category=SENIOR")
        self.assertEqual(res_senior.status_code, 200)
        data_senior = res_senior.json()["ranks"]
        self.assertEqual(len(data_senior), 9)
        self.assertTrue(all(r["officer_category"] == "SENIOR" for r in data_senior))

        # Test Junior
        res_junior = self.client.get(reverse("api-ranks") + "?category=JUNIOR")
        self.assertEqual(res_junior.status_code, 200)
        data_junior = res_junior.json()["ranks"]
        self.assertEqual(len(data_junior), 8)
        self.assertTrue(all(r["officer_category"] == "JUNIOR" for r in data_junior))

        # Test All
        res_all = self.client.get(reverse("api-ranks"))
        self.assertEqual(res_all.status_code, 200)
        self.assertEqual(len(res_all.json()["ranks"]), 17)

    def test_invalid_category_rank_combination_rejected_server_side(self):
        """Backend validation must reject when rank does not belong to selected category."""
        cfo_rank = Rank.objects.get(abbreviation="CFO")  # SENIOR

        # Model validation
        invalid_staff = NorminalRoll(
            staffID="99901",
            fullname="INVALID OFFICER",
            officer_category=Rank.OfficerCategory.JUNIOR,  # MISMATCH
            rank=cfo_rank,
            station=self.stn_komenda,
            birth_date=self.today - timedelta(days=365 * 30),
            ghana_card="GHA-999010000-1",
        )
        with self.assertRaises(ValidationError):
            invalid_staff.full_clean()

        # Form validation
        form_data = {
            "staffID": "99902",
            "fullname": "FORM TEST OFFICER",
            "officer_category": Rank.OfficerCategory.JUNIOR,
            "rank": cfo_rank.id,  # SENIOR rank submitted with JUNIOR category
            "station": self.stn_komenda.id,
            "birth_date": "1990-01-01",
            "ghana_card": "GHA-999020000-2",
            "payroll_status": "ACTIVE",
            "leave_status": "AT POST",
            "leave_type": "AT POST",
        }
        form = NorminalRollForm(data=form_data, user=self.national_admin)
        self.assertFalse(form.is_valid())
        self.assertIn("rank", form.errors)

    def test_valid_category_rank_combination_accepted(self):
        """Valid category and rank combination saves successfully and populates job_grade."""
        ado2_rank = Rank.objects.get(abbreviation="ADO II")
        staff = NorminalRoll.objects.create(
            staffID="99903",
            fullname="VALID OFFICER",
            officer_category=Rank.OfficerCategory.SENIOR,
            rank=ado2_rank,
            station=self.stn_komenda,
            birth_date=self.today - timedelta(days=365 * 28),
            ghana_card="GHA-999030000-3",
        )
        self.assertEqual(staff.job_grade, "ADO II")
        self.assertEqual(staff.rank_short, "ADO II")
        self.assertIn("Assistant Divisional Officer Grade Two", staff.rank_display)

    def test_rank_deletion_is_protected_when_referenced_by_personnel(self):
        """models.PROTECT prevents deletion of a Rank that is assigned to personnel."""
        fm_rank = Rank.objects.get(abbreviation="FM / FW")
        staff = NorminalRoll.objects.create(
            staffID="99904",
            fullname="PROTECTED RANK OFFICER",
            officer_category=Rank.OfficerCategory.JUNIOR,
            rank=fm_rank,
            station=self.stn_komenda,
            birth_date=self.today - timedelta(days=365 * 22),
            ghana_card="GHA-999040000-4",
        )
        from django.db.models import ProtectedError
        with self.assertRaises(ProtectedError):
            fm_rank.delete()

    def test_rank_filtering_respects_organisational_scope(self):
        """Station admin filtering only sees personnel within their assigned station."""
        fm_rank = Rank.objects.get(abbreviation="FM / FW")
        do1_rank = Rank.objects.get(abbreviation="DO I")

        # Komenda personnel (Station Admin's station)
        NorminalRoll.objects.create(
            staffID="K001",
            fullname="KOMENDA JUNIOR",
            officer_category=Rank.OfficerCategory.JUNIOR,
            rank=fm_rank,
            station=self.stn_komenda,
            birth_date=self.today - timedelta(days=365 * 24),
            ghana_card="GHA-100000001-1",
        )
        NorminalRoll.objects.create(
            staffID="K002",
            fullname="KOMENDA SENIOR",
            officer_category=Rank.OfficerCategory.SENIOR,
            rank=do1_rank,
            station=self.stn_komenda,
            birth_date=self.today - timedelta(days=365 * 35),
            ghana_card="GHA-100000002-2",
        )

        # Kumasi personnel (Different region/station)
        NorminalRoll.objects.create(
            staffID="A001",
            fullname="KUMASI JUNIOR",
            officer_category=Rank.OfficerCategory.JUNIOR,
            rank=fm_rank,
            station=self.stn_kumasi,
            birth_date=self.today - timedelta(days=365 * 24),
            ghana_card="GHA-200000001-1",
        )

        # Station admin logged in: Filter by Junior
        self.client.force_login(self.station_admin)
        res = self.client.get(reverse("personnel") + "?category=JUNIOR")
        self.assertEqual(res.status_code, 200)
        staff_list = res.context["staffs"]
        staff_ids = [s.staffID for s in staff_list]

        # Station admin sees Komenda junior, NOT Kumasi junior
        self.assertIn("K001", staff_ids)
        self.assertNotIn("K002", staff_ids)  # Senior, so excluded by category
        self.assertNotIn("A001", staff_ids)  # Kumasi, so excluded by scope!

    def test_existing_personnel_preserved_and_mapped(self):
        """Historical job_grade values are matched during seeding without data loss."""
        # Create legacy record with text job_grade before seeding
        legacy_staff = NorminalRoll.objects.create(
            staffID="LEGACY01",
            fullname="LEGACY FIREFIGHTER",
            job_grade="LFM",
            station=self.stn_komenda,
            birth_date=self.today - timedelta(days=365 * 26),
            ghana_card="GHA-777000001-1",
        )
        # Re-run seeder to map legacy record
        call_command("seed_gnfs_ranks")
        legacy_staff.refresh_from_db()

        self.assertIsNotNone(legacy_staff.rank)
        self.assertEqual(legacy_staff.rank.abbreviation, "LFM / LFW")
        self.assertEqual(legacy_staff.officer_category, Rank.OfficerCategory.JUNIOR)
        # Original job_grade is still preserved/accessible
        self.assertIn("LFM", legacy_staff.job_grade)


# ==============================================================================
# ENTERPRISE LEAVE MANAGEMENT SYSTEM TESTS
# ==============================================================================

from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from gnfs_komenda.models import (
    LeaveType, PublicHoliday, LeaveEntitlement, LeaveRequest,
    LeaveApprovalDecision, LeaveDefermentRequest, LeaveAdjustmentAudit
)
from gnfs_komenda.services.leave_calculator import calculate_leave_dates, is_working_day, get_next_working_day
from gnfs_komenda.services.leave_approval_service import (
    submit_leave_request, approve_leave_request, reject_leave_request,
    can_user_approve_leave, is_self_approval
)
from gnfs_komenda.services.leave_deferment_service import (
    request_leave_deferment, approve_leave_deferment, reject_leave_deferment, can_user_approve_deferment
)
from gnfs_komenda.services.leave_recall_service import recall_officer_from_leave


class GNFSLeaveManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_gnfs_structure")
        call_command("seed_gnfs_ranks")
        call_command("seed_public_holidays")
        call_command("seed_leave_types")

        cls.region = Region.objects.get(code="CR")
        cls.district = District.objects.filter(region=cls.region).first()
        cls.station = Station.objects.filter(district=cls.district).first()

        cls.rank_junior = Rank.objects.filter(officer_category="JUNIOR").first()
        cls.rank_senior = Rank.objects.filter(officer_category="SENIOR").first()

        # Users and Roles
        cls.su = User.objects.create_superuser(username="admin_su", email="su@gnfs.gov.gh", password="password123")
        
        cls.user_dfo = User.objects.create_user(username="dfo_user", email="dfo@gnfs.gov.gh", password="password123")
        UserAssignment.objects.create(user=cls.user_dfo, role="DFO", district=cls.district, active=True)

        cls.user_rfo = User.objects.create_user(username="rfo_user", email="rfo@gnfs.gov.gh", password="password123")
        UserAssignment.objects.create(user=cls.user_rfo, role="REGIONAL_ADMIN", region=cls.region, active=True)

        cls.user_officer = User.objects.create_user(username="GNFS1001", email="officer@gnfs.gov.gh", password="password123")
        UserAssignment.objects.create(user=cls.user_officer, role="STAFF", station=cls.station, active=True)

        cls.user_female = User.objects.create_user(username="GNFS3001", email="female@gnfs.gov.gh", password="password123")
        UserAssignment.objects.create(user=cls.user_female, role="STAFF", station=cls.station, active=True)

        # Personnel
        cls.junior_officer = NorminalRoll.objects.create(
            staffID="GNFS1001",
            fullname="KOFI JUNIOR FIREFIGHTER",
            rank=cls.rank_junior,
            job_grade=cls.rank_junior.abbreviation,
            officer_category="JUNIOR",
            station=cls.station,
            birth_date=date(1995, 5, 10),
            ghana_card="GHA-111222333-1"
        )

        cls.senior_officer = NorminalRoll.objects.create(
            staffID="GNFS2001",
            fullname="KWAME SENIOR COMMANDER",
            rank=cls.rank_senior,
            job_grade=cls.rank_senior.abbreviation,
            officer_category="SENIOR",
            gender="MALE",
            station=cls.station,
            birth_date=date(1980, 8, 20),
            ghana_card="GHA-222333444-2"
        )

        cls.female_officer = NorminalRoll.objects.create(
            staffID="GNFS3001",
            fullname="AMA FEMALE FIREFIGHTER",
            rank=cls.rank_junior,
            job_grade=cls.rank_junior.abbreviation,
            officer_category="JUNIOR",
            gender="FEMALE",
            station=cls.station,
            birth_date=date(1996, 4, 15),
            ghana_card="GHA-333444555-3"
        )

        # Run seeder to ensure entitlements exist for newly created personnel
        call_command("seed_leave_types")

    def test_leave_types_and_holidays_seeded(self):
        """Ensure all required Leave Types and Ghana statutory holidays are seeded."""
        self.assertGreaterEqual(LeaveType.objects.count(), 6)
        self.assertGreaterEqual(PublicHoliday.objects.count(), 40)
        
        annual = LeaveType.objects.get(code="ANNUAL")
        self.assertEqual(annual.calculation_mode, LeaveType.CalculationMode.WORKING_DAYS)
        self.assertEqual(annual.default_days_junior, 36)
        self.assertEqual(annual.default_days_senior, 42)

    def test_entitlement_initialization_junior_vs_senior(self):
        """Junior Officers receive 36 days annual leave; Senior Officers receive 42 days."""
        ent_junior = LeaveEntitlement.objects.get(
            personnel=self.junior_officer,
            leave_year=2026,
            leave_type__code="ANNUAL"
        )
        self.assertEqual(ent_junior.base_entitlement, 36)
        self.assertEqual(ent_junior.remaining_balance, 36)

        ent_senior = LeaveEntitlement.objects.get(
            personnel=self.senior_officer,
            leave_year=2026,
            leave_type__code="ANNUAL"
        )
        self.assertEqual(ent_senior.base_entitlement, 42)
        self.assertEqual(ent_senior.remaining_balance, 42)

    def test_working_days_calculation_excludes_weekends(self):
        """WORKING_DAYS calculation excludes Saturdays and Sundays."""
        # 2026-06-01 is a Monday
        start = date(2026, 6, 1)
        res = calculate_leave_dates(start_date=start, duration=5, calculation_mode="WORKING_DAYS")
        # 5 working days Mon-Fri: last leave date is Friday June 5, resume date is Monday June 8
        self.assertEqual(res['last_leave_date'], date(2026, 6, 5))
        self.assertEqual(res['resume_date'], date(2026, 6, 8))

        # 6 working days: last leave date should skip Saturday and Sunday to Monday June 8
        res6 = calculate_leave_dates(start_date=start, duration=6, calculation_mode="WORKING_DAYS")
        self.assertEqual(res6['last_leave_date'], date(2026, 6, 8))
        self.assertEqual(res6['resume_date'], date(2026, 6, 9))
        self.assertEqual(res6['weekend_days_count'], 2)

    def test_working_days_calculation_excludes_public_holidays(self):
        """WORKING_DAYS calculation excludes active public holidays."""
        # Configure a holiday on Wednesday 2026-06-03
        PublicHoliday.objects.create(
            name="Test National Day",
            date=date(2026, 6, 3),
            year=2026,
            active=True
        )
        start = date(2026, 6, 1) # Monday
        res = calculate_leave_dates(start_date=start, duration=5, calculation_mode="WORKING_DAYS")
        # Mon (1), Tue (2), Wed (Holiday-skipped), Thu (3), Fri (4), Sat/Sun (skipped), Mon (5)
        self.assertEqual(res['last_leave_date'], date(2026, 6, 8))
        self.assertEqual(res['resume_date'], date(2026, 6, 9))
        self.assertIn("2026-06-03", res['holidays_encountered'])

    def test_calendar_days_calculation_maternity(self):
        """Maternity leave of 90 calendar days includes weekends and holidays."""
        start = date(2026, 2, 1)
        res = calculate_leave_dates(start_date=start, duration=90, calculation_mode="CALENDAR_DAYS")
        expected_last = start + timedelta(days=89)
        self.assertEqual(res['last_leave_date'], expected_last)
        self.assertEqual(res['calendar_days_count'], 90)

    def test_terminal_leave_calendar_months_arithmetic(self):
        """Terminal leave uses exact calendar-month arithmetic with relativedelta."""
        start = date(2026, 1, 1)
        res = calculate_leave_dates(start_date=start, duration=6, calculation_mode="CALENDAR_MONTHS")
        self.assertEqual(res['last_leave_date'], date(2026, 6, 30))
        self.assertEqual(res['resume_date'], date(2026, 7, 1))

    def test_submit_leave_reserves_balance(self):
        """Submitting a leave request reserves days from LeaveEntitlement atomically."""
        annual_type = LeaveType.objects.get(code="ANNUAL")
        start = date(2026, 7, 6) # Monday
        
        req = submit_leave_request(
            personnel=self.junior_officer,
            leave_type=annual_type,
            start_date=start,
            duration=10,
            requested_by=self.user_officer,
            reason="Annual leave rest"
        )
        self.assertEqual(req.status, LeaveRequest.Status.PENDING_APPROVAL)
        self.assertEqual(req.working_days_count, 10)

        ent = LeaveEntitlement.objects.get(personnel=self.junior_officer, leave_year=2026, leave_type=annual_type)
        self.assertEqual(ent.days_reserved, 10)
        self.assertEqual(ent.remaining_balance, 26) # 36 - 10

    def test_insufficient_balance_rejected(self):
        """Submitting request exceeding remaining balance raises ValidationError."""
        annual_type = LeaveType.objects.get(code="ANNUAL")
        start = date(2026, 7, 6)
        
        with self.assertRaises(ValidationError):
            submit_leave_request(
                personnel=self.junior_officer,
                leave_type=annual_type,
                start_date=start,
                duration=40, # Exceeds 36
                requested_by=self.user_officer,
                reason="Too many days"
            )

    def test_anti_self_approval_strictly_blocked(self):
        """Officers are strictly forbidden from approving their own leave requests."""
        annual_type = LeaveType.objects.get(code="ANNUAL")
        req = submit_leave_request(
            personnel=self.junior_officer,
            leave_type=annual_type,
            start_date=date(2026, 7, 6),
            duration=5,
            requested_by=self.user_officer,
            reason="Rest"
        )
        self.assertTrue(is_self_approval(self.user_officer, req))
        self.assertFalse(can_user_approve_leave(self.user_officer, req))

        with self.assertRaises(PermissionDenied):
            approve_leave_request(req.id, self.user_officer, comments="Self approval attempt")

    def test_hierarchical_approval_and_balance_deduction(self):
        """DFO approves station officer leave; balance deducted from days_taken."""
        annual_type = LeaveType.objects.get(code="ANNUAL")
        req = submit_leave_request(
            personnel=self.junior_officer,
            leave_type=annual_type,
            start_date=date(2026, 7, 6),
            duration=10,
            requested_by=self.user_officer,
            reason="Approved rest"
        )

        self.assertTrue(can_user_approve_leave(self.user_dfo, req))
        approved_req = approve_leave_request(req.id, self.user_dfo, comments="Approved by DFO")
        
        self.assertEqual(approved_req.status, LeaveRequest.Status.APPROVED)
        self.assertEqual(approved_req.approved_by, self.user_dfo)

        ent = LeaveEntitlement.objects.get(personnel=self.junior_officer, leave_year=2026, leave_type=annual_type)
        self.assertEqual(ent.days_reserved, 0)
        self.assertEqual(ent.days_taken, 10)
        self.assertEqual(ent.remaining_balance, 26)

        # NorminalRoll operational fields synchronized
        self.junior_officer.refresh_from_db()
        self.assertEqual(self.junior_officer.start_leave, req.start_date)
        self.assertEqual(self.junior_officer.end_leave, req.last_leave_date)

    def test_rejection_restores_reserved_days(self):
        """Rejecting a pending leave restores reserved days back to balance."""
        annual_type = LeaveType.objects.get(code="ANNUAL")
        req = submit_leave_request(
            personnel=self.junior_officer,
            leave_type=annual_type,
            start_date=date(2026, 8, 3),
            duration=12,
            requested_by=self.user_officer,
            reason="Trip"
        )
        rejected_req = reject_leave_request(req.id, self.user_dfo, comments="Roster conflict")
        self.assertEqual(rejected_req.status, LeaveRequest.Status.REJECTED)

        ent = LeaveEntitlement.objects.get(personnel=self.junior_officer, leave_year=2026, leave_type=annual_type)
        self.assertEqual(ent.days_reserved, 0)
        self.assertEqual(ent.days_taken, 0)
        self.assertEqual(ent.remaining_balance, 36)

    def test_officer_recall_restores_balance_and_marks_at_post(self):
        """Recalling officer from leave restores unused days and sets leave_status to AT POST."""
        annual_type = LeaveType.objects.get(code="ANNUAL")
        start = date(2026, 9, 7) # Monday
        req = submit_leave_request(
            personnel=self.junior_officer,
            leave_type=annual_type,
            start_date=start,
            duration=10, # 2 weeks (Mon Sep 7 to Fri Sep 18)
            requested_by=self.user_officer,
            reason="Annual break"
        )
        approve_leave_request(req.id, self.user_dfo)

        # Recall effective on Monday Sep 14 (officer consumed week 1 = 5 working days)
        recall_d = date(2026, 9, 14)
        recalled = recall_officer_from_leave(req.id, self.user_dfo, recall_date=recall_d, recall_reason="Emergency call")

        self.assertEqual(recalled.status, LeaveRequest.Status.RECALLED)
        self.assertEqual(recalled.actual_days_consumed, 5)
        self.assertEqual(recalled.days_restored, 5)

        ent = LeaveEntitlement.objects.get(personnel=self.junior_officer, leave_year=2026, leave_type=annual_type)
        self.assertEqual(ent.days_taken, 5)
        self.assertEqual(ent.remaining_balance, 31) # 36 - 5

        self.junior_officer.refresh_from_db()
        self.assertTrue(self.junior_officer.called_off)
        self.assertEqual(self.junior_officer.leave_status, "AT POST")

    def test_leave_deferment_and_carry_forward(self):
        """Approved deferment carries forward approved days into target year entitlement."""
        deferment = request_leave_deferment(
            personnel=self.junior_officer,
            leave_year=2026,
            days_requested=15,
            reason="Operational exigency prevents full leave uptake",
            user=self.user_officer
        )
        self.assertEqual(deferment.status, LeaveDefermentRequest.Status.PENDING)

        approved = approve_leave_deferment(deferment.id, self.user_rfo, approved_days=10, remarks="Approved 10 days")
        self.assertEqual(approved.status, LeaveDefermentRequest.Status.APPROVED)
        self.assertEqual(approved.approved_days, 10)
        self.assertTrue(approved.is_carried_forward)

        # Check target year 2027 entitlement
        annual_type = LeaveType.objects.get(code="ANNUAL")
        ent_2027 = LeaveEntitlement.objects.get(personnel=self.junior_officer, leave_year=2027, leave_type=annual_type)
        self.assertEqual(ent_2027.carried_forward, 10)
        self.assertEqual(ent_2027.total_available, 46) # 36 base + 10 carry-forward
        self.assertEqual(ent_2027.remaining_balance, 46)

    def test_api_leave_calculate_view(self):
        """Test GET /api/leave/calculate/ endpoint."""
        self.client.force_login(self.user_dfo)
        res = self.client.get(reverse("api-leave-calculate") + "?start_date=2026-06-01&duration=5&calculation_mode=WORKING_DAYS")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['working_days_count'], 5)
        self.assertEqual(data['last_leave_date'], "2026-06-05")
        self.assertEqual(data['resume_date'], "2026-06-08")

    def test_api_leave_entitlement_view(self):
        """Test GET /api/leave/entitlement/ endpoint."""
        self.client.force_login(self.user_dfo)
        res = self.client.get(reverse("api-leave-entitlement") + f"?staffID={self.junior_officer.staffID}&year=2026")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['base_entitlement'], 36)
        self.assertEqual(data['remaining_balance'], 36)

    def test_overlapping_leave_request_rejected(self):
        """Officer cannot submit overlapping leave periods."""
        annual_type = LeaveType.objects.get(code="ANNUAL")
        req1 = submit_leave_request(
            personnel=self.junior_officer,
            leave_type=annual_type,
            start_date=date(2026, 10, 5),
            duration=5, # Oct 5 - Oct 9
            requested_by=self.user_officer
        )
        self.assertIsNotNone(req1)

        # Overlapping period: Oct 7 - Oct 14
        with self.assertRaises(ValidationError):
            submit_leave_request(
                personnel=self.junior_officer,
                leave_type=annual_type,
                start_date=date(2026, 10, 7),
                duration=5,
                requested_by=self.user_officer
            )

    def test_sick_leave_date_range_mode(self):
        """Sick leave uses DATE_RANGE mode without deducting from annual balance."""
        sick_type = LeaveType.objects.get(code="SICK")
        start = date(2026, 11, 2)
        end = date(2026, 11, 13)
        res = calculate_leave_dates(start_date=start, duration=12, calculation_mode="DATE_RANGE", end_date=end)
        self.assertEqual(res['last_leave_date'], end)
        self.assertEqual(res['calendar_days_count'], 12)

    def test_unauthorized_staff_cannot_approve_peer_leave(self):
        """A peer staff officer cannot approve another officer's leave."""
        annual_type = LeaveType.objects.get(code="ANNUAL")
        req = submit_leave_request(
            personnel=self.senior_officer,
            leave_type=annual_type,
            start_date=date(2026, 10, 5),
            duration=5,
            requested_by=self.user_dfo
        )
        self.assertFalse(can_user_approve_leave(self.user_officer, req))

    def test_leave_balance_manual_adjustment_with_audit(self):
        """National admin can adjust leave balance and creates an audit record."""
        annual_type = LeaveType.objects.get(code="ANNUAL")
        ent = LeaveEntitlement.objects.get(personnel=self.junior_officer, leave_year=2026, leave_type=annual_type)
        prev_bal = ent.remaining_balance

        self.client.force_login(self.su)
        res = self.client.post(reverse("leave-adjust-balance", kwargs={"entitlement_id": ent.id}), {
            "days_adjusted": 4,
            "reason": "Official service commendation credit"
        })
        self.assertEqual(res.status_code, 302)

        ent.refresh_from_db()
        self.assertEqual(ent.adjustments, 4)
        self.assertEqual(ent.remaining_balance, prev_bal + 4)

        audit = LeaveAdjustmentAudit.objects.filter(entitlement=ent).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.days_adjusted, 4)
        self.assertEqual(audit.previous_balance, prev_bal)
        self.assertEqual(audit.new_balance, prev_bal + 4)

    def test_staff_details_page_shows_leave_balances(self):
        """GET /staff/details/<staffID>/ displays leave balances table."""
        self.client.force_login(self.user_dfo)
        res = self.client.get(reverse("staff-detail", kwargs={"staffID": self.junior_officer.staffID}))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Current Year Entitlement Balances")
        self.assertContains(res, "Annual Leave")

    def test_gender_restrictions_paternity_and_maternity(self):
        """Male cannot take Maternity leave; Female cannot take Paternity leave."""
        maternity = LeaveType.objects.get(code="MATERNITY")
        paternity = LeaveType.objects.get(code="PATERNITY")

        # Male attempting Maternity -> raises ValidationError
        with self.assertRaises(ValidationError) as ctx:
            submit_leave_request(
                personnel=self.junior_officer,  # Male
                leave_type=maternity,
                start_date=date(2026, 4, 6),
                duration=90,
                requested_by=self.user_officer
            )
        self.assertIn("Male personnel cannot apply for Maternity Leave", str(ctx.exception))

        # Female attempting Paternity -> raises ValidationError
        with self.assertRaises(ValidationError) as ctx:
            submit_leave_request(
                personnel=self.female_officer,  # Female
                leave_type=paternity,
                start_date=date(2026, 4, 6),
                duration=5,
                requested_by=self.user_female
            )
        self.assertIn("Female personnel cannot apply for Paternity Leave", str(ctx.exception))

        # Male applying for Paternity -> Succeeds (5 working days)
        req_pat = submit_leave_request(
            personnel=self.junior_officer,
            leave_type=paternity,
            start_date=date(2026, 5, 4), # Monday
            duration=5,
            requested_by=self.user_officer
        )
        self.assertEqual(req_pat.status, LeaveRequest.Status.PENDING_APPROVAL)
        self.assertEqual(req_pat.working_days_count, 5)

        # Female applying for Maternity -> Succeeds (90 calendar days)
        req_mat = submit_leave_request(
            personnel=self.female_officer,
            leave_type=maternity,
            start_date=date(2026, 5, 1),
            duration=90,
            requested_by=self.user_female
        )
        self.assertEqual(req_mat.status, LeaveRequest.Status.PENDING_APPROVAL)
        self.assertEqual(req_mat.calendar_days_count, 90)

    def test_proportionate_annual_leave_calculation_and_balance(self):
        """Officers can take proportionate / part annual leave, preserving remaining balance."""
        annual = LeaveType.objects.get(code="ANNUAL")
        ent = LeaveEntitlement.objects.get(
            personnel=self.junior_officer,
            leave_year=2026,
            leave_type=annual
        )
        self.assertEqual(ent.remaining_balance, 36)

        # Step 1: Officer takes 10 days proportionate leave
        req1 = submit_leave_request(
            personnel=self.junior_officer,
            leave_type=annual,
            start_date=date(2026, 8, 3), # Monday
            duration=10,
            requested_by=self.user_officer,
            is_proportionate=True
        )
        self.assertTrue(req1.is_proportionate)
        ent.refresh_from_db()
        self.assertEqual(ent.days_reserved, 10)
        self.assertEqual(ent.remaining_balance, 26)

        # Superior approves
        approve_leave_request(req1.id, self.user_dfo)
        ent.refresh_from_db()
        self.assertEqual(ent.days_taken, 10)
        self.assertEqual(ent.days_reserved, 0)
        self.assertEqual(ent.remaining_balance, 26)

        # Step 2: Officer later requests another 15 days proportionate leave
        req2 = submit_leave_request(
            personnel=self.junior_officer,
            leave_type=annual,
            start_date=date(2026, 10, 5), # Monday
            duration=15,
            requested_by=self.user_officer,
            is_proportionate=True
        )
        ent.refresh_from_db()
        self.assertEqual(ent.days_reserved, 15)
        self.assertEqual(ent.remaining_balance, 11)

        approve_leave_request(req2.id, self.user_dfo)
        ent.refresh_from_db()
        self.assertEqual(ent.days_taken, 25)
        self.assertEqual(ent.remaining_balance, 11)

    def test_sick_leave_requires_medical_reference_or_attachment(self):
        """Sick Leave validation enforces hospital/medical reference or excuse duty document."""
        sick = LeaveType.objects.get(code="SICK")
        form = LeaveRequestForm(data={
            "personnel": self.junior_officer.id,
            "leave_type": sick.id,
            "start_date": "2026-06-01",
            "duration": 5,
            "reason": "Severe malaria"
        })
        self.assertFalse(form.is_valid())
        self.assertIn("medical_reference", form.errors)

        # Valid when hospital name is provided
        valid_form = LeaveRequestForm(data={
            "personnel": self.junior_officer.id,
            "leave_type": sick.id,
            "start_date": "2026-06-01",
            "duration": 5,
            "medical_reference": "Komenda Health Centre - Dr. Arhin",
            "reason": "Severe malaria"
        })
        self.assertTrue(valid_form.is_valid())


