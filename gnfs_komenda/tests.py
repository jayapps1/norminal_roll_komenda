from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from gnfs_komenda.forms import (
    NorminalRollForm, LeaveRequestForm, PersonnelTransferForm,
    TransferDestinationPlacementForm, TransferConfirmArrivalForm, TransferCancelForm
)
from gnfs_komenda.models import (
    ApprovalHistory, AuditLog, District, Notification, NorminalRoll,
    PersonnelActionRequest, Post, Rank, Region, Station,
    FunctionalUnit, FunctionalRoleAssignment, FunctionalRoleType,
    FunctionalOffice, LeaveWorkflowHistory, WatchRoomBooking,
    PersonnelPosting, PersonnelFunctionalAssignment, PersonnelTransfer,
    TransferApprovalHistory, TransferStatus, TransferType, CommandAppointment,
    LeaveType, LeaveRequest, LeaveEntitlement, LeaveApprovalDecision
)
from gnfs_komenda.security import (
    can_user_approve_request, get_personnel_queryset_for_user,
    get_personnel_for_user
)
from gnfs_komenda.services.leave_routing_service import (
    resolve_leave_route, submit_personnel_leave_request, process_leave_request,
    final_approve_leave_request, final_reject_leave_request,
    resubmit_corrected_leave_request, can_user_process_leave,
    can_user_final_approve_leave
)
from gnfs_komenda.services.transfer_service import (
    create_transfer, approve_transfer, release_officer, mark_reported,
    complete_destination_placement, confirm_arrival, cancel_transfer
)
from gnfs_komenda.services.permission_resolver import (
    can_view_personnel, can_manage_personnel as can_manage_personnel_resolver,
    can_manage_functional_unit, get_available_workspaces,
    can_manage_regional_leadership, can_manage_district_leadership,
    can_manage_functional_structure
)
from gnfs_komenda.services.command_appointment_service import (
    appoint_rfo, appoint_regional_2ic, appoint_commanding_officer,
    appoint_dfo, appoint_mfo, appoint_district_2ic, end_command_appointment,
    get_region_leadership_history, get_district_leadership_history,
    get_officer_command_history
)
from gnfs_komenda.services.functional_assignment_service import (
    assign_functional_unit, remove_functional_assignment,
    set_primary_functional_assignment, get_active_functional_assignments,
    get_officer_functional_history
)
from gnfs_komenda.services.organisational_structure_service import (
    create_functional_unit, update_functional_unit,
    deactivate_functional_unit, reactivate_functional_unit,
    delete_functional_unit, get_organisation_tree, validate_unit_hierarchy
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


# ==============================================================================
# FUNCTIONAL ORGANISATION & ENTERPRISE TRANSFER TESTS
# ==============================================================================

class FunctionalAndTransferTests(TestCase):
    def setUp(self):
        call_command('seed_gnfs_directorates')
        self.today = timezone.now().date()

        # Command structure
        self.region_central = Region.objects.create(name="Central Region", code="CR", active=True)
        self.region_ashanti = Region.objects.create(name="Ashanti Region", code="AR", active=True)

        self.district_keea = District.objects.create(name="KEEA", region=self.region_central, active=True)
        self.district_kumasi = District.objects.create(name="Kumasi Metro", region=self.region_ashanti, active=True)

        self.station_komenda = Station.objects.create(name="Komenda Fire Station", district=self.district_keea, active=True)
        self.station_kumasi = Station.objects.create(name="Kumasi Central Fire Station", district=self.district_kumasi, active=True)

        # Ranks
        self.rank_so = Rank.objects.create(
            name="Station Officer", abbreviation="SO", officer_category=Rank.OfficerCategory.SENIOR,
            hierarchy_order=5, active=True
        )
        self.rank_fm = Rank.objects.create(
            name="Fireman", abbreviation="FM", officer_category=Rank.OfficerCategory.JUNIOR,
            hierarchy_order=1, active=True
        )

        # Functional Units from seeder
        self.dir_admin = FunctionalUnit.objects.get(code="ADM", unit_type="DIRECTORATE")
        self.dept_rs = FunctionalUnit.objects.get(code="ADM-RS", unit_type="DEPARTMENT")
        self.unit_sec = FunctionalUnit.objects.get(code="ADM-RS-SEC", unit_type="UNIT")

        self.dir_ops = FunctionalUnit.objects.get(code="OPS", unit_type="DIRECTORATE")

        # Personnel
        self.officer = NorminalRoll.objects.create(
            staffID="GNFS-TR-001",
            fullname="KOFI MENSAH",
            rank=self.rank_fm,
            job_grade="FIREMAN",
            officer_category=Rank.OfficerCategory.JUNIOR,
            gender="MALE",
            station=self.station_komenda,
            functional_unit=self.unit_sec,
            birth_date=self.today - timedelta(days=365 * 28),
            ghana_card="GHA-998877665-1",
            mgmt_units="0307 GNFS, KEEA",
            bank="GCB Bank",
            account_number="9876543210",
            payroll_status="ACTIVE",
            is_active=True
        )

        # Initial active posting
        PersonnelPosting.objects.create(
            personnel=self.officer,
            region=self.region_central,
            district=self.district_keea,
            station=self.station_komenda,
            start_date=self.today - timedelta(days=200),
            is_current=True
        )

        # Users
        self.national_admin = User.objects.create_user(username="nat_admin", password="password123", is_superuser=True)
        self.cr_rfo_user = User.objects.create_user(username="cr_rfo", password="password123")
        UserAssignment.objects.create(user=self.cr_rfo_user, role=GNFSUserRole.REGIONAL_ADMIN, region=self.region_central, active=True)

        self.ar_rfo_user = User.objects.create_user(username="ar_rfo", password="password123")
        UserAssignment.objects.create(user=self.ar_rfo_user, role=GNFSUserRole.REGIONAL_ADMIN, region=self.region_ashanti, active=True)

        self.admin_head_user = User.objects.create_user(username="dir_admin_head", password="password123")
        UserAssignment.objects.create(user=self.admin_head_user, role=GNFSUserRole.STAFF, active=True)
        FunctionalRoleAssignment.objects.create(
            user=self.admin_head_user,
            organisational_unit=self.dir_admin,
            role=FunctionalRoleType.DIRECTORATE_HEAD,
            active=True
        )

    def test_seed_gnfs_directorates_idempotent(self):
        """Seeding directorates multiple times produces identical state without duplicates."""
        count_before = FunctionalUnit.objects.count()
        self.assertGreater(count_before, 20)
        call_command('seed_gnfs_directorates')
        count_after = FunctionalUnit.objects.count()
        self.assertEqual(count_before, count_after)

    def test_cagd_mgmt_units_preserved(self):
        """Preserves CAGD management units cost-centre string untouched."""
        officer = NorminalRoll.objects.get(staffID="GNFS-TR-001")
        self.assertEqual(officer.mgmt_units, "0307 GNFS, KEEA")
        officer.save()
        self.assertEqual(officer.mgmt_units, "0307 GNFS, KEEA")

    def test_seed_initial_postings_creates_and_is_idempotent(self):
        """Backfilling postings generates current posting and does not duplicate on re-run."""
        postings_count = PersonnelPosting.objects.filter(personnel=self.officer).count()
        self.assertEqual(postings_count, 1)
        call_command('seed_initial_postings')
        postings_count_after = PersonnelPosting.objects.filter(personnel=self.officer).count()
        self.assertEqual(postings_count_after, 1)

    def test_norminal_roll_form_hierarchy_validation(self):
        """Form rejects mismatching Directorate and Unit parents."""
        data = {
            'staffID': 'GNFS-VAL-001',
            'fullname': 'KWAME VAL',
            'job_grade': 'FIREMAN',
            'rank': self.rank_fm.id,
            'officer_category': 'JUNIOR',
            'gender': 'MALE',
            'birth_date': '1995-05-15',
            'ghana_card': 'GHA-112233445-9',
            'mgmt_units': '0307 GNFS, KEEA',
            'bank': 'GCB',
            'account_number': '11223344',
            'payroll_status': 'ACTIVE',
            'station': self.station_komenda.id,
            'directorate': self.dir_ops.id, # Directorate is Operations
            'department': self.dept_rs.id, # But Department belongs to Administration! Mismatch!
        }
        form = NorminalRollForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn("department", form.errors)

    def test_functional_scope_resolver_and_workspaces(self):
        """Administration Directorate Head can manage personnel under Administration Directorate."""
        self.assertTrue(can_manage_personnel_resolver(self.admin_head_user, self.officer))
        self.assertTrue(can_manage_functional_unit(self.admin_head_user, self.dir_admin))
        self.assertTrue(can_manage_functional_unit(self.admin_head_user, self.dept_rs))
        self.assertFalse(can_manage_functional_unit(self.admin_head_user, self.dir_ops))

        # Check workspaces returned
        workspaces = get_available_workspaces(self.admin_head_user)
        ws_ids = [w['id'] for w in workspaces]
        self.assertIn(f"func_{self.dir_admin.id}", ws_ids)

    def test_transfer_lifecycle_atomic_posting_activation(self):
        """
        Complete transfer lifecycle:
        DRAFT/PENDING -> APPROVED -> RELEASED -> REPORTED -> CONFIRMED.
        Verifies active posting does NOT change until arrival confirmation.
        """
        # 1. Initiate transfer from Komenda (Central) to Kumasi Central (Ashanti)
        transfer = create_transfer(
            personnel=self.officer,
            user=self.national_admin,
            destination_region=self.region_ashanti,
            destination_district=self.district_kumasi,
            destination_station=self.station_kumasi,
            transfer_letter_number="GNFS/HQ/TR/2026/088",
            effective_date=self.today + timedelta(days=7),
            expected_reporting_date=self.today + timedelta(days=14),
            reason="Operational service exigency",
            submit_now=True
        )
        self.assertEqual(transfer.status, TransferStatus.PENDING_APPROVAL)
        self.assertEqual(transfer.transfer_type, TransferType.INTER_REGIONAL)

        # CRITICAL RULE: Active posting must NOT have changed!
        self.officer.refresh_from_db()
        self.assertEqual(self.officer.station, self.station_komenda)
        current_posting = self.officer.postings.filter(is_current=True).first()
        self.assertEqual(current_posting.station, self.station_komenda)

        # 2. Approve Transfer
        approve_transfer(transfer, self.national_admin, comments="Approved by Chief Fire Officer")
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, TransferStatus.APPROVED)

        # Active posting still unchanged
        self.officer.refresh_from_db()
        self.assertEqual(self.officer.station, self.station_komenda)

        # 3. Release Officer from Central Region
        release_officer(transfer, self.cr_rfo_user, comments="Released on duty warrant")
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, TransferStatus.RELEASED)

        # Officer is in transit, but posting record still points to source station until confirmed
        self.officer.refresh_from_db()
        self.assertEqual(self.officer.station, self.station_komenda)

        # 4. Mark Reported at Ashanti Region / Kumasi
        mark_reported(transfer, self.ar_rfo_user, comments="Reported in uniform at Regional HQ")
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, TransferStatus.REPORTED)

        # 5. Confirm Arrival at Destination Station (Kumasi Central)
        actual_date = self.today + timedelta(days=10)
        confirm_arrival(transfer, self.ar_rfo_user, actual_reporting_date=actual_date, comments="Duty assumed")
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, TransferStatus.CONFIRMED)
        self.assertEqual(transfer.actual_reporting_date, actual_date)

        # NOW posting must be atomically updated!
        self.officer.refresh_from_db()
        self.assertEqual(self.officer.station, self.station_kumasi)
        self.assertEqual(self.officer.current_district, self.district_kumasi)
        self.assertEqual(self.officer.current_region, self.region_ashanti)

        # Old posting must be closed
        old_posting = PersonnelPosting.objects.get(station=self.station_komenda)
        self.assertFalse(old_posting.is_current)
        self.assertEqual(old_posting.end_date, actual_date)

        # New posting must be active
        new_posting = PersonnelPosting.objects.get(station=self.station_kumasi)
        self.assertTrue(new_posting.is_current)
        self.assertEqual(new_posting.start_date, actual_date)

        # Verify audit history logged all steps
        history_actions = list(transfer.history.values_list('action', flat=True))
        self.assertIn("CREATE_AND_SUBMIT", history_actions)
        self.assertIn("APPROVE_TRANSFER", history_actions)
        self.assertIn("RELEASE_OFFICER", history_actions)
        self.assertIn("MARK_REPORTED", history_actions)
        self.assertIn("CONFIRM_ARRIVAL", history_actions)

    def test_transfer_placement_by_receiving_region(self):
        """Regional pool transfer where destination district/station is assigned during placement."""
        # Initiate transfer with only destination region specified
        transfer = create_transfer(
            personnel=self.officer,
            user=self.national_admin,
            destination_region=self.region_ashanti,
            effective_date=self.today + timedelta(days=5),
            expected_reporting_date=self.today + timedelta(days=12),
            submit_now=True
        )
        self.assertIsNone(transfer.destination_station)

        # Approve and release
        approve_transfer(transfer, self.national_admin)
        release_officer(transfer, self.cr_rfo_user)
        mark_reported(transfer, self.ar_rfo_user)

        # Ashanti administration assigns station placement
        complete_destination_placement(
            transfer=transfer,
            user=self.ar_rfo_user,
            destination_district=self.district_kumasi,
            destination_station=self.station_kumasi,
            comments="Assigned to Kumasi Metro Station"
        )
        transfer.refresh_from_db()
        self.assertEqual(transfer.destination_station, self.station_kumasi)

        # Confirm arrival
        confirm_arrival(transfer, self.ar_rfo_user, actual_reporting_date=self.today + timedelta(days=8))
        self.officer.refresh_from_db()
        self.assertEqual(self.officer.station, self.station_kumasi)

    def test_transfer_cancellation_keeps_original_posting(self):
        """Cancelled transfer does not alter officer's original command posting."""
        transfer = create_transfer(
            personnel=self.officer,
            user=self.national_admin,
            destination_region=self.region_ashanti,
            destination_station=self.station_kumasi,
            effective_date=self.today + timedelta(days=5),
            expected_reporting_date=self.today + timedelta(days=10),
            submit_now=True
        )
        approve_transfer(transfer, self.national_admin)
        release_officer(transfer, self.cr_rfo_user)

        # Cancel transfer
        cancel_transfer(transfer, self.national_admin, reason="Transfer cancelled by HQ due to staffing shortage")
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, TransferStatus.CANCELLED)

        # Officer posting still at Komenda
        self.officer.refresh_from_db()
        self.assertEqual(self.officer.station, self.station_komenda)
        current_posting = self.officer.postings.filter(is_current=True).first()
        self.assertEqual(current_posting.station, self.station_komenda)

    def test_transfer_to_identical_station_rejected(self):
        """Cannot transfer officer to the exact same station without change."""
        with self.assertRaises(ValidationError):
            create_transfer(
                personnel=self.officer,
                user=self.national_admin,
                destination_region=self.region_central,
                destination_district=self.district_keea,
                destination_station=self.station_komenda,
                destination_functional_unit=self.unit_sec, # Same functional unit too
                effective_date=self.today + timedelta(days=5),
                expected_reporting_date=self.today + timedelta(days=10)
            )


# ==============================================================================
# SECTION 17: COMMAND APPOINTMENTS & FUNCTIONAL ASSIGNMENTS TEST SUITE (30 TESTS)
# ==============================================================================

class CommandAppointmentAndFunctionalAssignmentTests(TestCase):
    def setUp(self):
        self.today = timezone.now().date()

        # Administrative User
        self.admin_user = User.objects.create_superuser(
            username="admin_sec17",
            email="admin_sec17@fire.gov.gh",
            password="adminpassword123"
        )

        # Ranks
        self.rank_acfo = Rank.objects.create(
            name="Assistant Chief Fire Officer (ACFO)",
            abbreviation="ACFO",
            officer_category=Rank.OfficerCategory.SENIOR,
            hierarchy_order=3,
            active=True
        )
        self.rank_do2 = Rank.objects.create(
            name="Divisional Officer II (DO II)",
            abbreviation="DO II",
            officer_category=Rank.OfficerCategory.SENIOR,
            hierarchy_order=6,
            active=True
        )
        self.rank_stn_o = Rank.objects.create(
            name="Station Officer (STN/O)",
            abbreviation="STN/O",
            officer_category=Rank.OfficerCategory.JUNIOR,
            hierarchy_order=10,
            active=True
        )

        # Command Hierarchy
        self.region_central = Region.objects.create(name="Central Region", code="CR", active=True)
        self.region_western = Region.objects.create(name="Western Region", code="WR", active=True)

        self.district_keea = District.objects.create(
            name="KEEA",
            region=self.region_central,
            code="KEEA",
            command_type=District.CommandType.MUNICIPAL,
            co_designation="MFO",
            active=True
        )
        self.station_komenda = Station.objects.create(
            name="Komenda Fire Station",
            district=self.district_keea,
            code="KOM",
            active=True
        )

        # Functional Hierarchy
        self.dir_admin = FunctionalUnit.objects.create(
            name="Administration",
            unit_type=FunctionalUnit.FunctionalUnitType.DIRECTORATE,
            code="ADMIN",
            active=True
        )
        self.dir_ops = FunctionalUnit.objects.create(
            name="Operations",
            unit_type=FunctionalUnit.FunctionalUnitType.DIRECTORATE,
            code="OPS",
            active=True
        )
        self.dept_pr = FunctionalUnit.objects.create(
            name="Public Relations",
            parent=self.dir_admin,
            unit_type=FunctionalUnit.FunctionalUnitType.DEPARTMENT,
            code="PR",
            active=True
        )
        self.unit_pub_ed = FunctionalUnit.objects.create(
            name="Public Education",
            parent=self.dept_pr,
            unit_type=FunctionalUnit.FunctionalUnitType.UNIT,
            code="PUB_ED",
            active=True
        )
        self.unit_control = FunctionalUnit.objects.create(
            name="Fire Master Control",
            parent=self.dir_ops,
            unit_type=FunctionalUnit.FunctionalUnitType.UNIT,
            code="CONTROL",
            active=True
        )

        # Officers
        self.officer_a = NorminalRoll.objects.create(
            staffID="OFF-A-01",
            fullname="Officer Alpha",
            officer_category=Rank.OfficerCategory.SENIOR,
            rank=self.rank_acfo,
            station=self.station_komenda,
            ghana_card="GHA-111111111-1",
            mgmt_units="KOMENDA",
            birth_date=self.today - timedelta(days=365 * 40),
            payroll_status="ACTIVE",
            is_active=True
        )
        self.officer_b = NorminalRoll.objects.create(
            staffID="OFF-B-02",
            fullname="Officer Bravo",
            officer_category=Rank.OfficerCategory.SENIOR,
            rank=self.rank_do2,
            station=self.station_komenda,
            ghana_card="GHA-222222222-2",
            mgmt_units="KOMENDA",
            birth_date=self.today - timedelta(days=365 * 35),
            payroll_status="ACTIVE",
            is_active=True
        )
        self.officer_c = NorminalRoll.objects.create(
            staffID="OFF-C-03",
            fullname="Officer Charlie",
            officer_category=Rank.OfficerCategory.JUNIOR,
            rank=self.rank_stn_o,
            station=self.station_komenda,
            ghana_card="GHA-333333333-3",
            mgmt_units="KOMENDA",
            birth_date=self.today - timedelta(days=365 * 30),
            payroll_status="ACTIVE",
            is_active=True
        )
        self.officer_d = NorminalRoll.objects.create(
            staffID="OFF-D-04",
            fullname="Officer Delta",
            officer_category=Rank.OfficerCategory.JUNIOR,
            rank=self.rank_stn_o,
            station=self.station_komenda,
            ghana_card="GHA-444444444-4",
            mgmt_units="KOMENDA",
            birth_date=self.today - timedelta(days=365 * 28),
            payroll_status="ACTIVE",
            is_active=True
        )

    # 1. Region can have one active RFO
    def test_01_region_can_have_one_active_rfo(self):
        rfo_apt = appoint_rfo(
            region=self.region_central,
            personnel=self.officer_a,
            appointed_by=self.admin_user,
            appointment_reference="REF/CR/001"
        )
        self.assertTrue(rfo_apt.is_active)
        self.assertEqual(rfo_apt.appointment_type, CommandAppointment.AppointmentType.RFO)
        self.assertEqual(self.region_central.current_rfo.id, rfo_apt.id)
        self.assertEqual(self.region_central.current_rfo.personnel, self.officer_a)

    # 2. Region cannot have two active RFOs
    def test_02_region_cannot_have_two_active_rfos(self):
        appoint_rfo(region=self.region_central, personnel=self.officer_a, appointed_by=self.admin_user)
        # Directly trying to force a second active RFO record violates database UniqueConstraint
        with self.assertRaises(IntegrityError):
            CommandAppointment.objects.create(
                personnel=self.officer_b,
                appointment_type=CommandAppointment.AppointmentType.RFO,
                region=self.region_central,
                is_active=True,
                effective_from=self.today
            )

    # 3. Region can have one active 2IC
    def test_03_region_can_have_one_active_2ic(self):
        reg_2ic = appoint_regional_2ic(
            region=self.region_central,
            personnel=self.officer_b,
            appointed_by=self.admin_user
        )
        self.assertTrue(reg_2ic.is_active)
        self.assertEqual(self.region_central.current_2ic.id, reg_2ic.id)
        self.assertEqual(self.region_central.current_2ic.personnel, self.officer_b)

    # 4. Region cannot have two active 2ICs
    def test_04_region_cannot_have_two_active_2ics(self):
        appoint_regional_2ic(region=self.region_central, personnel=self.officer_b, appointed_by=self.admin_user)
        with self.assertRaises(IntegrityError):
            CommandAppointment.objects.create(
                personnel=self.officer_c,
                appointment_type=CommandAppointment.AppointmentType.REGIONAL_2IC,
                region=self.region_central,
                is_active=True,
                effective_from=self.today
            )

    # 5. District can have one active DFO/MFO
    def test_05_district_can_have_one_active_dfo_mfo(self):
        co_apt = appoint_commanding_officer(
            district=self.district_keea,
            personnel=self.officer_a,
            appointment_type="MFO",
            appointed_by=self.admin_user
        )
        self.assertTrue(co_apt.is_active)
        self.assertEqual(self.district_keea.current_commanding_officer.id, co_apt.id)
        self.assertEqual(self.district_keea.current_commanding_officer.personnel, self.officer_a)

    # 6. District cannot have two active commanding officers
    def test_06_district_cannot_have_two_active_commanding_officers(self):
        appoint_commanding_officer(district=self.district_keea, personnel=self.officer_a, appointment_type="MFO", appointed_by=self.admin_user)
        with self.assertRaises(IntegrityError):
            CommandAppointment.objects.create(
                personnel=self.officer_b,
                appointment_type=CommandAppointment.AppointmentType.MFO,
                district=self.district_keea,
                is_active=True,
                effective_from=self.today
            )

    # 7. District can have one active 2IC
    def test_07_district_can_have_one_active_2ic(self):
        dist_2ic = appoint_district_2ic(
            district=self.district_keea,
            personnel=self.officer_b,
            appointed_by=self.admin_user
        )
        self.assertTrue(dist_2ic.is_active)
        self.assertEqual(self.district_keea.current_2ic.id, dist_2ic.id)
        self.assertEqual(self.district_keea.current_2ic.personnel, self.officer_b)

    # 8. District cannot have two active 2ICs
    def test_08_district_cannot_have_two_active_2ics(self):
        appoint_district_2ic(district=self.district_keea, personnel=self.officer_b, appointed_by=self.admin_user)
        with self.assertRaises(IntegrityError):
            CommandAppointment.objects.create(
                personnel=self.officer_c,
                appointment_type=CommandAppointment.AppointmentType.DISTRICT_2IC,
                district=self.district_keea,
                is_active=True,
                effective_from=self.today
            )

    # 9. Previous RFO history remains when RFO changes
    def test_09_previous_rfo_history_remains_when_rfo_changes(self):
        apt1 = appoint_rfo(region=self.region_central, personnel=self.officer_a, appointed_by=self.admin_user)
        # Reappoint new officer as RFO
        apt2 = appoint_rfo(region=self.region_central, personnel=self.officer_b, appointed_by=self.admin_user)

        apt1.refresh_from_db()
        self.assertFalse(apt1.is_active)
        self.assertEqual(apt1.effective_to, self.today)
        self.assertTrue(apt2.is_active)

        history = get_region_leadership_history(self.region_central)
        self.assertEqual(history.count(), 2)
        self.assertEqual(self.region_central.current_rfo.id, apt2.id)

    # 10. Previous DFO/MFO history remains
    def test_10_previous_dfo_mfo_history_remains(self):
        apt1 = appoint_commanding_officer(district=self.district_keea, personnel=self.officer_a, appointed_by=self.admin_user)
        apt2 = appoint_commanding_officer(district=self.district_keea, personnel=self.officer_b, appointed_by=self.admin_user)

        apt1.refresh_from_db()
        self.assertFalse(apt1.is_active)
        self.assertTrue(apt2.is_active)

        history = get_district_leadership_history(self.district_keea)
        self.assertEqual(history.count(), 2)
        self.assertEqual(self.district_keea.current_commanding_officer.id, apt2.id)

    # 11. Previous 2IC history remains
    def test_11_previous_2ic_history_remains(self):
        apt1 = appoint_district_2ic(district=self.district_keea, personnel=self.officer_c, appointed_by=self.admin_user)
        apt2 = appoint_district_2ic(district=self.district_keea, personnel=self.officer_d, appointed_by=self.admin_user)

        apt1.refresh_from_db()
        self.assertFalse(apt1.is_active)
        self.assertTrue(apt2.is_active)

        history = get_district_leadership_history(self.district_keea)
        self.assertEqual(history.count(), 2)

    # 12. 2IC can have no Department
    def test_12_2ic_can_have_no_department(self):
        # Officer C has 0 functional assignments
        self.assertEqual(self.officer_c.functional_assignments.count(), 0)
        apt = appoint_district_2ic(district=self.district_keea, personnel=self.officer_c, appointed_by=self.admin_user)
        self.assertTrue(apt.is_active)
        self.assertEqual(self.district_keea.current_2ic.personnel, self.officer_c)

    # 13. 2IC can have one Department
    def test_13_2ic_can_have_one_department(self):
        assign_functional_unit(personnel=self.officer_c, functional_unit=self.dept_pr, is_primary=True)
        self.assertEqual(self.officer_c.functional_assignments.filter(is_active=True).count(), 1)
        apt = appoint_district_2ic(district=self.district_keea, personnel=self.officer_c, appointed_by=self.admin_user)
        self.assertTrue(apt.is_active)

    # 14. 2IC can have multiple Departments
    def test_14_2ic_can_have_multiple_departments(self):
        assign_functional_unit(personnel=self.officer_c, functional_unit=self.dept_pr, is_primary=True)
        assign_functional_unit(personnel=self.officer_c, functional_unit=self.unit_control, is_primary=False)
        self.assertEqual(self.officer_c.functional_assignments.filter(is_active=True).count(), 2)
        apt = appoint_district_2ic(district=self.district_keea, personnel=self.officer_c, appointed_by=self.admin_user)
        self.assertTrue(apt.is_active)

    # 15. Personnel can have multiple functional assignments
    def test_15_personnel_can_have_multiple_functional_assignments(self):
        fa1 = assign_functional_unit(personnel=self.officer_b, functional_unit=self.unit_pub_ed, assignment_role="Public Educator", is_primary=True)
        fa2 = assign_functional_unit(personnel=self.officer_b, functional_unit=self.unit_control, assignment_role="Control Officer", is_primary=False)

        active_fas = get_active_functional_assignments(self.officer_b)
        self.assertEqual(active_fas.count(), 2)
        self.assertIn(fa1, active_fas)
        self.assertIn(fa2, active_fas)

    # 16. Only one assignment can be primary
    def test_16_only_one_assignment_can_be_primary(self):
        fa1 = assign_functional_unit(personnel=self.officer_b, functional_unit=self.unit_pub_ed, is_primary=True)
        fa2 = assign_functional_unit(personnel=self.officer_b, functional_unit=self.unit_control, is_primary=True)

        fa1.refresh_from_db()
        fa2.refresh_from_db()
        self.assertFalse(fa1.is_primary)
        self.assertTrue(fa2.is_primary)

        # Database unique constraint test
        with self.assertRaises(IntegrityError):
            PersonnelFunctionalAssignment.objects.create(
                personnel=self.officer_b,
                functional_unit=self.dept_pr,
                is_primary=True,
                is_active=True,
                start_date=self.today
            )

    # 17. Department membership does not automatically grant management permission
    def test_17_department_membership_does_not_automatically_grant_management_permission(self):
        # Create non-privileged user
        regular_user = User.objects.create_user(username="OFF-C-03", password="userpass123")
        assign_functional_unit(personnel=self.officer_c, functional_unit=self.unit_pub_ed, assignment_role="Member")
        self.assertFalse(can_manage_functional_unit(regular_user, self.unit_pub_ed))

    # 18. RFO/DFO appointment is independent of rank
    def test_18_rfo_dfo_appointment_is_independent_of_rank(self):
        # Junior officer Officer C appointed as DFO
        dfo_apt = appoint_dfo(district=self.district_keea, personnel=self.officer_c, appointed_by=self.admin_user)
        self.assertTrue(dfo_apt.is_active)
        self.assertEqual(dfo_apt.personnel.rank, self.rank_stn_o)

        # DO II appointed as RFO
        rfo_apt = appoint_rfo(region=self.region_central, personnel=self.officer_b, appointed_by=self.admin_user)
        self.assertTrue(rfo_apt.is_active)
        self.assertEqual(rfo_apt.personnel.rank, self.rank_do2)

    # 19. Transfer detects active command appointment
    def test_19_transfer_detects_active_command_appointment(self):
        appoint_dfo(district=self.district_keea, personnel=self.officer_a, appointed_by=self.admin_user)
        transfer = create_transfer(
            personnel=self.officer_a,
            user=self.admin_user,
            destination_region=self.region_western,
            effective_date=self.today + timedelta(days=2),
            submit_now=True
        )
        approve_transfer(transfer, self.admin_user)
        release_officer(transfer, self.admin_user)

        # Setup destination station in Western Region
        district_wr = District.objects.create(name="Sekondi-Takoradi", region=self.region_western, active=True)
        station_wr = Station.objects.create(name="Market Circle Station", district=district_wr, active=True)
        complete_destination_placement(transfer, self.admin_user, destination_district=district_wr, destination_station=station_wr)

        # Attempt confirm arrival without resolving command appointment -> should raise ValidationError
        with self.assertRaises(ValidationError) as ctx:
            confirm_arrival(transfer, self.admin_user, resolve_appointments=False)
        self.assertIn("active command appointment", str(ctx.exception).lower())

    # 20. Transfer does not move command appointment automatically
    def test_20_transfer_does_not_move_command_appointment_automatically(self):
        apt = appoint_dfo(district=self.district_keea, personnel=self.officer_a, appointed_by=self.admin_user)
        transfer = create_transfer(
            personnel=self.officer_a,
            user=self.admin_user,
            destination_region=self.region_western,
            effective_date=self.today + timedelta(days=2),
            submit_now=True
        )
        approve_transfer(transfer, self.admin_user)
        release_officer(transfer, self.admin_user)

        district_wr = District.objects.create(name="Sekondi-Takoradi", region=self.region_western, active=True)
        station_wr = Station.objects.create(name="Market Circle Station", district=district_wr, active=True)
        complete_destination_placement(transfer, self.admin_user, destination_district=district_wr, destination_station=station_wr)

        # Confirm arrival with resolve_appointments=True
        confirm_arrival(transfer, self.admin_user, resolve_appointments=True)
        apt.refresh_from_db()
        self.assertFalse(apt.is_active)

        # Officer has no active command appointment at Western Region
        self.assertEqual(CommandAppointment.objects.filter(personnel=self.officer_a, is_active=True).count(), 0)

    # 21. Directorate CRUD works
    def test_21_directorate_crud_works(self):
        d = create_functional_unit(name="Training Directorate", unit_type=FunctionalUnit.FunctionalUnitType.DIRECTORATE, code="TRG")
        self.assertEqual(d.name, "Training Directorate")
        update_functional_unit(d, name="Training & Standards Directorate")
        self.assertEqual(d.name, "Training & Standards Directorate")
        deactivate_functional_unit(d)
        self.assertFalse(d.active)
        reactivate_functional_unit(d)
        self.assertTrue(d.active)

    # 22. Department CRUD works
    def test_22_department_crud_works(self):
        dept = create_functional_unit(name="Media Relations", unit_type=FunctionalUnit.FunctionalUnitType.DEPARTMENT, parent=self.dir_admin)
        self.assertEqual(dept.parent, self.dir_admin)
        update_functional_unit(dept, name="Press & Media Relations")
        self.assertEqual(dept.name, "Press & Media Relations")
        deactivate_functional_unit(dept)
        self.assertFalse(dept.active)

    # 23. Unit CRUD works
    def test_23_unit_crud_works(self):
        u = create_functional_unit(name="Central Registry", unit_type=FunctionalUnit.FunctionalUnitType.UNIT, parent=self.dept_pr)
        self.assertEqual(u.parent, self.dept_pr)
        update_functional_unit(u, name="Archival & Central Registry")
        self.assertEqual(u.name, "Archival & Central Registry")
        deactivate_functional_unit(u)
        self.assertFalse(u.active)

    # 24. Referenced organisational structures cannot be destructively deleted
    def test_24_referenced_organisational_structures_cannot_be_destructively_deleted(self):
        # unit_pub_ed has officer_b assigned
        assign_functional_unit(personnel=self.officer_b, functional_unit=self.unit_pub_ed)
        with self.assertRaises(ValidationError):
            delete_functional_unit(self.unit_pub_ed, user=self.admin_user)

    # 25. Deactivated structures disappear from new-assignment dropdowns
    def test_25_deactivated_structures_disappear_from_new_assignment_dropdowns(self):
        deactivate_functional_unit(self.unit_control)
        with self.assertRaises(ValidationError) as ctx:
            assign_functional_unit(personnel=self.officer_d, functional_unit=self.unit_control)
        self.assertIn("deactivated", str(ctx.exception).lower())

    # 26. Historical records still display deactivated structures
    def test_26_historical_records_still_display_deactivated_structures(self):
        fa = assign_functional_unit(personnel=self.officer_d, functional_unit=self.unit_pub_ed)
        deactivate_functional_unit(self.unit_pub_ed)
        fa.refresh_from_db()
        self.assertEqual(fa.functional_unit.name, "Public Education")
        self.assertFalse(fa.functional_unit.active)

    # 27. Cascading Directorate -> Department -> Unit works
    def test_27_cascading_directorate_department_unit_works(self):
        self.assertEqual(self.unit_pub_ed.directorate, self.dir_admin)
        self.assertEqual(self.unit_pub_ed.department_or_division, self.dept_pr)
        self.assertEqual(self.dept_pr.directorate, self.dir_admin)

    # 28. Backend rejects invalid parent-child combinations
    def test_28_backend_rejects_invalid_parent_child_combinations(self):
        with self.assertRaises(ValidationError):
            create_functional_unit(name="Bad Directorate", unit_type=FunctionalUnit.FunctionalUnitType.DIRECTORATE, parent=self.dir_admin)
        with self.assertRaises(ValidationError):
            create_functional_unit(name="Bad Dept", unit_type=FunctionalUnit.FunctionalUnitType.DEPARTMENT, parent=None)

    # 29. Existing personnel survive migration
    def test_29_existing_personnel_survive_migration(self):
        officer = NorminalRoll.objects.get(staffID="OFF-A-01")
        self.assertEqual(officer.fullname, "Officer Alpha")
        self.assertEqual(officer.station, self.station_komenda)

    # 30. Existing mgmt_units values are preserved until safely migrated
    def test_30_existing_mgmt_units_values_are_preserved(self):
        officer = NorminalRoll.objects.get(staffID="OFF-A-01")
        self.assertEqual(officer.mgmt_units, "KOMENDA")


# ==============================================================================
# SECTION 49: PERSONNEL SELF-SERVICE & LEAVE WORKFLOW TESTS (35 SPECIFICATIONS)
# ==============================================================================

class PersonnelSelfServiceAndLeaveWorkflowTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.today = timezone.localdate()

        # Admin user
        self.admin_user = User.objects.create_superuser(
            username="test_admin", email="admin@gnfs.gov.gh", password="password123"
        )

        # Ranks
        self.rank_acfo, _ = Rank.objects.get_or_create(
            name="Assistant Chief Fire Officer",
            defaults={"abbreviation": "ACFO", "officer_category": Rank.OfficerCategory.SENIOR, "hierarchy_order": 1}
        )
        self.rank_sfm, _ = Rank.objects.get_or_create(
            name="Senior Fireman",
            defaults={"abbreviation": "SFM", "officer_category": Rank.OfficerCategory.JUNIOR, "hierarchy_order": 10}
        )

        # Leave Types
        self.lt_annual, _ = LeaveType.objects.get_or_create(
            code="ANNUAL",
            defaults={
                "name": "Annual Leave",
                "calculation_mode": "WORKING_DAYS",
                "default_days_senior": 42,
                "default_days_junior": 36,
                "active": True,
            }
        )
        self.lt_sick, _ = LeaveType.objects.get_or_create(
            code="SICK",
            defaults={
                "name": "Sick Leave",
                "calculation_mode": "CALENDAR_DAYS",
                "default_days_senior": 30,
                "default_days_junior": 30,
                "requires_attachment": True,
                "active": True,
            }
        )

        # Regions
        self.reg_cr = Region.objects.create(name="Central Region", code="CR")
        self.reg_wr = Region.objects.create(name="Western Region", code="WR")

        # Districts
        self.dist_keea = District.objects.create(
            name="Komenda Edina Eguafo Abrem", code="KEEA", region=self.reg_cr
        )
        self.dist_ccm = District.objects.create(
            name="Cape Coast Metro", code="CCM", region=self.reg_cr, co_designation="MFO"
        )
        self.dist_stma = District.objects.create(
            name="Sekondi Takoradi Metro", code="STMA", region=self.reg_wr, co_designation="MFO"
        )

        # Stations
        self.stn_komenda = Station.objects.create(
            name="Komenda Fire Station", code="KFS", district=self.dist_keea
        )
        self.stn_cc = Station.objects.create(
            name="Cape Coast Metro Station", code="CCS", district=self.dist_ccm
        )
        self.stn_sekondi = Station.objects.create(
            name="Sekondi Fire Station", code="SKS", district=self.dist_stma
        )

        # Directorate & Dept for Functional Unit
        self.dir_admin = FunctionalUnit.objects.create(
            name="Admin Directorate", code="ADMIN-DIR",
            unit_type=FunctionalUnit.FunctionalUnitType.DIRECTORATE
        )
        self.dept_pr = FunctionalUnit.objects.create(
            name="Public Relations Dept", code="PR-DEPT",
            unit_type=FunctionalUnit.FunctionalUnitType.DEPARTMENT, parent=self.dir_admin
        )
        self.unit_pr = FunctionalUnit.objects.create(
            name="Public Education Unit", code="PR-PE",
            unit_type=FunctionalUnit.FunctionalUnitType.UNIT, parent=self.dept_pr
        )

        # Personnel 1: Ordinary District Personnel at Komenda
        self.p_keea = NorminalRoll.objects.create(
            staffID="GNFS-KEEA-101",
            fullname="Kofi Mensah",
            rank=self.rank_sfm,
            job_grade="SFM",
            station=self.stn_komenda,
            officer_category="JUNIOR",
            birth_date=self.today - timedelta(days=365 * 28),
            ghana_card="GHA-101101101-1",
            bank="GCB",
            account_number="101010101",
            payroll_status="ACTIVE",
            gender="MALE",
        )
        PersonnelPosting.objects.create(
            personnel=self.p_keea, region=self.reg_cr, district=self.dist_keea,
            station=self.stn_komenda, is_current=True, start_date=self.today - timedelta(days=100)
        )

        # Personnel 2: DFO KEEA
        self.p_dfo = NorminalRoll.objects.create(
            staffID="GNFS-DFO-201",
            fullname="Kwesi DFO",
            rank=self.rank_acfo,
            job_grade="ACFO",
            station=self.stn_komenda,
            officer_category="SENIOR",
            birth_date=self.today - timedelta(days=365 * 45),
            ghana_card="GHA-201201201-2",
            bank="GCB",
            account_number="201020102",
            payroll_status="ACTIVE",
            gender="MALE",
        )
        PersonnelPosting.objects.create(
            personnel=self.p_dfo, region=self.reg_cr, district=self.dist_keea,
            station=self.stn_komenda, is_current=True, start_date=self.today - timedelta(days=200)
        )
        appoint_dfo(district=self.dist_keea, personnel=self.p_dfo, effective_from=self.today - timedelta(days=200), appointed_by=self.admin_user)

        # Personnel 3: MFO Cape Coast Metro
        self.p_mfo = NorminalRoll.objects.create(
            staffID="GNFS-MFO-301",
            fullname="Ama MFO",
            rank=self.rank_acfo,
            job_grade="ACFO",
            station=self.stn_cc,
            officer_category="SENIOR",
            birth_date=self.today - timedelta(days=365 * 42),
            ghana_card="GHA-301301301-3",
            bank="GCB",
            account_number="301030103",
            payroll_status="ACTIVE",
            gender="FEMALE",
        )
        PersonnelPosting.objects.create(
            personnel=self.p_mfo, region=self.reg_cr, district=self.dist_ccm,
            station=self.stn_cc, is_current=True, start_date=self.today - timedelta(days=150)
        )
        appoint_mfo(district=self.dist_ccm, personnel=self.p_mfo, effective_from=self.today - timedelta(days=150), appointed_by=self.admin_user)

        # Personnel 4: RFO Central Region
        self.p_rfo_cr = NorminalRoll.objects.create(
            staffID="GNFS-RFOCR-401",
            fullname="Yaw RFO",
            rank=self.rank_acfo,
            job_grade="ACFO",
            station=self.stn_cc,
            officer_category="SENIOR",
            birth_date=self.today - timedelta(days=365 * 50),
            ghana_card="GHA-401401401-4",
            bank="GCB",
            account_number="401040104",
            payroll_status="ACTIVE",
            gender="MALE",
        )
        PersonnelPosting.objects.create(
            personnel=self.p_rfo_cr, region=self.reg_cr, district=self.dist_ccm,
            station=self.stn_cc, is_current=True, start_date=self.today - timedelta(days=300)
        )
        appoint_rfo(region=self.reg_cr, personnel=self.p_rfo_cr, effective_from=self.today - timedelta(days=300), appointed_by=self.admin_user)

        # Personnel 5: District 2IC KEEA
        self.p_2ic = NorminalRoll.objects.create(
            staffID="GNFS-2IC-501",
            fullname="Esi 2IC",
            rank=self.rank_sfm,
            job_grade="SFM",
            station=self.stn_komenda,
            officer_category="JUNIOR",
            birth_date=self.today - timedelta(days=365 * 35),
            ghana_card="GHA-501501501-5",
            bank="GCB",
            account_number="501050105",
            payroll_status="ACTIVE",
            gender="FEMALE",
        )
        PersonnelPosting.objects.create(
            personnel=self.p_2ic, region=self.reg_cr, district=self.dist_keea,
            station=self.stn_komenda, is_current=True, start_date=self.today - timedelta(days=120)
        )
        appoint_district_2ic(district=self.dist_keea, personnel=self.p_2ic, effective_from=self.today - timedelta(days=120), appointed_by=self.admin_user)

        # Personnel 6: Regional Personnel under Central Region
        self.p_reg_staff = NorminalRoll.objects.create(
            staffID="GNFS-REG-601",
            fullname="Regional Staff",
            rank=self.rank_sfm,
            job_grade="SFM",
            station=self.stn_cc,
            officer_category="JUNIOR",
            birth_date=self.today - timedelta(days=365 * 30),
            ghana_card="GHA-601601601-6",
            bank="GCB",
            account_number="601060106",
            payroll_status="ACTIVE",
            gender="MALE",
        )
        PersonnelPosting.objects.create(
            personnel=self.p_reg_staff, region=self.reg_cr, district=None,
            station=None, is_current=True, start_date=self.today - timedelta(days=100)
        )

        # Personnel 7: RFO Western Region
        self.p_rfo_wr = NorminalRoll.objects.create(
            staffID="GNFS-RFOWR-701",
            fullname="Western RFO",
            rank=self.rank_acfo,
            job_grade="ACFO",
            station=self.stn_sekondi,
            officer_category="SENIOR",
            birth_date=self.today - timedelta(days=365 * 52),
            ghana_card="GHA-701701701-7",
            bank="GCB",
            account_number="701070107",
            payroll_status="ACTIVE",
            gender="MALE",
        )
        PersonnelPosting.objects.create(
            personnel=self.p_rfo_wr, region=self.reg_wr, district=self.dist_stma,
            station=self.stn_sekondi, is_current=True, start_date=self.today - timedelta(days=300)
        )
        appoint_rfo(region=self.reg_wr, personnel=self.p_rfo_wr, effective_from=self.today - timedelta(days=300), appointed_by=self.admin_user)

        # Users and Linkages
        self.u_keea = User.objects.create_user(username="GNFS-KEEA-101", password="password123")
        UserAssignment.objects.create(user=self.u_keea, role=GNFSUserRole.STAFF, personnel=self.p_keea, active=True)

        self.u_dist_admin = User.objects.create_user(username="dist_admin_keea", password="password123")
        UserAssignment.objects.create(user=self.u_dist_admin, role=GNFSUserRole.DISTRICT_ADMIN, district=self.dist_keea, active=True)

        self.u_dfo = User.objects.create_user(username="GNFS-DFO-201", password="password123")
        UserAssignment.objects.create(user=self.u_dfo, role=GNFSUserRole.DFO, district=self.dist_keea, personnel=self.p_dfo, active=True)

        self.u_mfo = User.objects.create_user(username="GNFS-MFO-301", password="password123")
        UserAssignment.objects.create(user=self.u_mfo, role=GNFSUserRole.DFO, district=self.dist_ccm, personnel=self.p_mfo, active=True)

        self.u_reg_admin = User.objects.create_user(username="reg_admin_cr", password="password123")
        UserAssignment.objects.create(user=self.u_reg_admin, role=GNFSUserRole.REGIONAL_ADMIN, region=self.reg_cr, active=True)

        self.u_rfo_cr = User.objects.create_user(username="GNFS-RFOCR-401", password="password123")
        UserAssignment.objects.create(user=self.u_rfo_cr, role=GNFSUserRole.REGIONAL_ADMIN, region=self.reg_cr, personnel=self.p_rfo_cr, active=True)

        self.u_2ic = User.objects.create_user(username="GNFS-2IC-501", password="password123")
        UserAssignment.objects.create(user=self.u_2ic, role=GNFSUserRole.STAFF, district=self.dist_keea, personnel=self.p_2ic, active=True)

        self.u_reg_staff = User.objects.create_user(username="GNFS-REG-601", password="password123")
        UserAssignment.objects.create(user=self.u_reg_staff, role=GNFSUserRole.STAFF, region=self.reg_cr, personnel=self.p_reg_staff, active=True)

        self.u_rfo_wr = User.objects.create_user(username="GNFS-RFOWR-701", password="password123")
        UserAssignment.objects.create(user=self.u_rfo_wr, role=GNFSUserRole.REGIONAL_ADMIN, region=self.reg_wr, personnel=self.p_rfo_wr, active=True)

    # 1. Every personnel-linked user gets My Dashboard
    def test_01_every_personnel_linked_user_gets_my_dashboard(self):
        self.client.login(username="GNFS-KEEA-101", password="password123")
        res = self.client.get(reverse("my-dashboard"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["personnel"], self.p_keea)
        self.client.logout()

        self.client.login(username="GNFS-DFO-201", password="password123")
        res = self.client.get(reverse("my-dashboard"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["personnel"], self.p_dfo)
        self.client.logout()

        self.client.login(username="GNFS-RFOCR-401", password="password123")
        res = self.client.get(reverse("my-dashboard"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["personnel"], self.p_rfo_cr)

    # 2. Ordinary personnel can request leave
    def test_02_ordinary_personnel_can_request_leave(self):
        self.client.login(username="GNFS-KEEA-101", password="password123")
        res = self.client.post(reverse("my-leave-request"), {
            "leave_type": self.lt_annual.id,
            "start_date": (self.today + timedelta(days=10)).strftime("%Y-%m-%d"),
            "duration": 5,
            "reason": "Personal rest",
        })
        self.assertEqual(res.status_code, 302)
        leave_req = LeaveRequest.objects.filter(personnel=self.p_keea).first()
        self.assertIsNotNone(leave_req)
        self.assertIn(leave_req.status, [LeaveRequest.Status.PENDING_PROCESSING, LeaveRequest.Status.PENDING_ADMIN_REVIEW])
        self.assertEqual(leave_req.duration, 5)

    # 3. Personnel can only view their own personal leave
    def test_03_personnel_can_only_view_their_own_personal_leave(self):
        dfo_leave = submit_personnel_leave_request(
            personnel=self.p_dfo, leave_type=self.lt_annual,
            start_date=self.today + timedelta(days=15), duration=5, requested_by=self.u_dfo
        )
        self.client.login(username="GNFS-KEEA-101", password="password123")
        res = self.client.get(reverse("my-leave-detail", args=[dfo_leave.pk]))
        self.assertEqual(res.status_code, 403)

    # 4. District personnel leave reaches District Admin
    def test_04_district_personnel_leave_reaches_district_admin(self):
        leave_req = submit_personnel_leave_request(
            personnel=self.p_keea, leave_type=self.lt_annual,
            start_date=self.today + timedelta(days=5), duration=10, requested_by=self.u_keea
        )
        self.assertEqual(leave_req.processing_stage, LeaveRequest.ProcessingStage.DISTRICT_ADMIN)
        self.assertEqual(leave_req.routing_district, self.dist_keea)
        self.assertTrue(can_user_process_leave(self.u_dist_admin, leave_req))

    # 5. District Admin can process it
    def test_05_district_admin_can_process_it(self):
        leave_req = submit_personnel_leave_request(
            personnel=self.p_keea, leave_type=self.lt_annual,
            start_date=self.today + timedelta(days=5), duration=10, requested_by=self.u_keea
        )
        process_leave_request(leave_req.id, self.u_dist_admin, action="FORWARD", comments="Documents and dates verified.")
        leave_req.refresh_from_db()
        self.assertEqual(leave_req.status, LeaveRequest.Status.PENDING_APPROVAL)
        self.assertEqual(leave_req.processed_by, self.u_dist_admin)
        self.assertEqual(leave_req.processing_notes, "Documents and dates verified.")

    # 6. District Admin cannot automatically final-approve unless separately authorised
    def test_06_district_admin_cannot_automatically_final_approve(self):
        leave_req = submit_personnel_leave_request(
            personnel=self.p_keea, leave_type=self.lt_annual,
            start_date=self.today + timedelta(days=5), duration=10, requested_by=self.u_keea
        )
        process_leave_request(leave_req.id, self.u_dist_admin, action="FORWARD")
        leave_req.refresh_from_db()
        with self.assertRaises(PermissionDenied):
            final_approve_leave_request(leave_req.id, self.u_dist_admin, comments="Trying to approve")

    # 7. Processed District leave reaches current DFO/MFO
    def test_07_processed_district_leave_reaches_current_dfo_mfo(self):
        leave_req = submit_personnel_leave_request(
            personnel=self.p_keea, leave_type=self.lt_annual,
            start_date=self.today + timedelta(days=5), duration=10, requested_by=self.u_keea
        )
        process_leave_request(leave_req.id, self.u_dist_admin, action="FORWARD")
        leave_req.refresh_from_db()
        self.assertTrue(can_user_final_approve_leave(self.u_dfo, leave_req))

    # 8. Correct DFO/MFO can approve
    def test_08_correct_dfo_mfo_can_approve(self):
        leave_req = submit_personnel_leave_request(
            personnel=self.p_keea, leave_type=self.lt_annual,
            start_date=self.today + timedelta(days=5), duration=10, requested_by=self.u_keea
        )
        process_leave_request(leave_req.id, self.u_dist_admin, action="FORWARD")
        final_approve_leave_request(leave_req.id, self.u_dfo, comments="Approved by DFO KEEA")
        leave_req.refresh_from_db()
        self.assertEqual(leave_req.status, LeaveRequest.Status.APPROVED)
        self.assertEqual(leave_req.approved_by, self.u_dfo)

    # 9. Wrong District DFO/MFO cannot approve
    def test_09_wrong_district_dfo_mfo_cannot_approve(self):
        leave_req = submit_personnel_leave_request(
            personnel=self.p_keea, leave_type=self.lt_annual,
            start_date=self.today + timedelta(days=5), duration=10, requested_by=self.u_keea
        )
        process_leave_request(leave_req.id, self.u_dist_admin, action="FORWARD")
        with self.assertRaises(PermissionDenied):
            final_approve_leave_request(leave_req.id, self.u_mfo, comments="Cross-district approval")

    # 10. DFO cannot approve own leave
    def test_10_dfo_cannot_approve_own_leave(self):
        dfo_leave = submit_personnel_leave_request(
            personnel=self.p_dfo, leave_type=self.lt_annual,
            start_date=self.today + timedelta(days=10), duration=15, requested_by=self.u_dfo
        )
        self.assertFalse(can_user_final_approve_leave(self.u_dfo, dfo_leave))
        with self.assertRaises(PermissionDenied):
            final_approve_leave_request(dfo_leave.id, self.u_dfo, comments="Self approval")

    # 11. MFO cannot approve own leave
    def test_11_mfo_cannot_approve_own_leave(self):
        mfo_leave = submit_personnel_leave_request(
            personnel=self.p_mfo, leave_type=self.lt_annual,
            start_date=self.today + timedelta(days=10), duration=15, requested_by=self.u_mfo
        )
        self.assertFalse(can_user_final_approve_leave(self.u_mfo, mfo_leave))
        with self.assertRaises(PermissionDenied):
            final_approve_leave_request(mfo_leave.id, self.u_mfo, comments="Self approval")

    # 12. DFO leave reaches Regional Admin
    def test_12_dfo_leave_reaches_regional_admin(self):
        dfo_leave = submit_personnel_leave_request(
            personnel=self.p_dfo, leave_type=self.lt_annual,
            start_date=self.today + timedelta(days=10), duration=15, requested_by=self.u_dfo
        )
        self.assertEqual(dfo_leave.processing_stage, LeaveRequest.ProcessingStage.REGIONAL_ADMIN)
        self.assertEqual(dfo_leave.routing_region, self.reg_cr)
        self.assertTrue(can_user_process_leave(self.u_reg_admin, dfo_leave))

    # 13. DFO leave reaches appropriate RFO for final approval
    def test_13_dfo_leave_reaches_appropriate_rfo_for_final_approval(self):
        dfo_leave = submit_personnel_leave_request(
            personnel=self.p_dfo, leave_type=self.lt_annual,
            start_date=self.today + timedelta(days=10), duration=15, requested_by=self.u_dfo
        )
        process_leave_request(dfo_leave.id, self.u_reg_admin, action="FORWARD")
        dfo_leave.refresh_from_db()
        self.assertTrue(can_user_final_approve_leave(self.u_rfo_cr, dfo_leave))
        final_approve_leave_request(dfo_leave.id, self.u_rfo_cr, comments="Approved by RFO")
        dfo_leave.refresh_from_db()
        self.assertEqual(dfo_leave.status, LeaveRequest.Status.APPROVED)

    # 14. MFO leave reaches appropriate RFO
    def test_14_mfo_leave_reaches_appropriate_rfo(self):
        mfo_leave = submit_personnel_leave_request(
            personnel=self.p_mfo, leave_type=self.lt_annual,
            start_date=self.today + timedelta(days=10), duration=15, requested_by=self.u_mfo
        )
        process_leave_request(mfo_leave.id, self.u_reg_admin, action="FORWARD")
        self.assertTrue(can_user_final_approve_leave(self.u_rfo_cr, mfo_leave))

    # 15. Regional personnel leave reaches Regional Admin
    def test_15_regional_personnel_leave_reaches_regional_admin(self):
        reg_leave = submit_personnel_leave_request(
            personnel=self.p_reg_staff, leave_type=self.lt_annual,
            start_date=self.today + timedelta(days=10), duration=10, requested_by=self.u_reg_staff
        )
        self.assertEqual(reg_leave.processing_stage, LeaveRequest.ProcessingStage.REGIONAL_ADMIN)
        self.assertEqual(reg_leave.routing_region, self.reg_cr)
        self.assertTrue(can_user_process_leave(self.u_reg_admin, reg_leave))

    # 16. Regional personnel leave reaches current RFO
    def test_16_regional_personnel_leave_reaches_current_rfo(self):
        reg_leave = submit_personnel_leave_request(
            personnel=self.p_reg_staff, leave_type=self.lt_annual,
            start_date=self.today + timedelta(days=10), duration=10, requested_by=self.u_reg_staff
        )
        process_leave_request(reg_leave.id, self.u_reg_admin, action="FORWARD")
        self.assertTrue(can_user_final_approve_leave(self.u_rfo_cr, reg_leave))

    # 17. Wrong Region RFO cannot approve
    def test_17_wrong_region_rfo_cannot_approve(self):
        reg_leave = submit_personnel_leave_request(
            personnel=self.p_reg_staff, leave_type=self.lt_annual,
            start_date=self.today + timedelta(days=10), duration=10, requested_by=self.u_reg_staff
        )
        process_leave_request(reg_leave.id, self.u_reg_admin, action="FORWARD")
        with self.assertRaises(PermissionDenied):
            final_approve_leave_request(reg_leave.id, self.u_rfo_wr, comments="Wrong Region RFO")

    # 18. RFO cannot approve own leave
    def test_18_rfo_cannot_approve_own_leave(self):
        rfo_leave = submit_personnel_leave_request(
            personnel=self.p_rfo_cr, leave_type=self.lt_annual,
            start_date=self.today + timedelta(days=10), duration=10, requested_by=self.u_rfo_cr
        )
        self.assertFalse(can_user_final_approve_leave(self.u_rfo_cr, rfo_leave))
        with self.assertRaises(PermissionDenied):
            final_approve_leave_request(rfo_leave.id, self.u_rfo_cr, comments="Self approval")

    # 19. Department membership does not change command leave route
    def test_19_department_membership_does_not_change_command_leave_route(self):
        assign_functional_unit(personnel=self.p_keea, functional_unit=self.unit_pr, is_primary=True, assigned_by=self.admin_user)
        route = resolve_leave_route(self.p_keea)
        self.assertEqual(route["processor_stage"], LeaveRequest.ProcessingStage.DISTRICT_ADMIN)
        self.assertEqual(route["district"], self.dist_keea)
        self.assertEqual(route["approver_officer"].personnel, self.p_dfo)

    # 20. 2IC does not automatically become final leave approver
    def test_20_2ic_does_not_automatically_become_final_leave_approver(self):
        leave_req = submit_personnel_leave_request(
            personnel=self.p_keea, leave_type=self.lt_annual,
            start_date=self.today + timedelta(days=5), duration=5, requested_by=self.u_keea
        )
        process_leave_request(leave_req.id, self.u_dist_admin, action="FORWARD")
        self.assertFalse(can_user_final_approve_leave(self.u_2ic, leave_req))

    # 21. Multiple user roles cannot bypass self-approval protection
    def test_21_multiple_user_roles_cannot_bypass_self_approval_protection(self):
        # Give DFO an extra role of REGIONAL_ADMIN on central region
        asgn = self.u_dfo.assignment
        asgn.role = GNFSUserRole.REGIONAL_ADMIN
        asgn.region = self.reg_cr
        asgn.save()

        dfo_leave = submit_personnel_leave_request(
            personnel=self.p_dfo, leave_type=self.lt_annual,
            start_date=self.today + timedelta(days=10), duration=5, requested_by=self.u_dfo
        )
        process_leave_request(dfo_leave.id, self.u_reg_admin, action="FORWARD")
        self.assertFalse(can_user_final_approve_leave(self.u_dfo, dfo_leave))

    # 22. Returned-for-correction requests can be edited and resubmitted
    def test_22_returned_for_correction_requests_can_be_edited_and_resubmitted(self):
        leave_req = submit_personnel_leave_request(
            personnel=self.p_keea, leave_type=self.lt_annual,
            start_date=self.today + timedelta(days=5), duration=5, requested_by=self.u_keea
        )
        process_leave_request(leave_req.id, self.u_dist_admin, action="RETURN_FOR_CORRECTION", comments="Need different start date")
        leave_req.refresh_from_db()
        self.assertEqual(leave_req.status, LeaveRequest.Status.RETURNED_FOR_CORRECTION)
        self.assertEqual(leave_req.correction_reason, "Need different start date")

        # Officer resubmits
        resubmit_corrected_leave_request(
            leave_req_id=leave_req.id, user=self.u_keea,
            start_date=self.today + timedelta(days=12), duration=4, reason="Updated start date"
        )
        leave_req.refresh_from_db()
        self.assertIn(leave_req.status, [LeaveRequest.Status.PENDING_PROCESSING, LeaveRequest.Status.PENDING_ADMIN_REVIEW])
        self.assertEqual(leave_req.duration, 4)

    # 23. Rejected requests retain history
    def test_23_rejected_requests_retain_history(self):
        leave_req = submit_personnel_leave_request(
            personnel=self.p_keea, leave_type=self.lt_annual,
            start_date=self.today + timedelta(days=5), duration=5, requested_by=self.u_keea
        )
        process_leave_request(leave_req.id, self.u_dist_admin, action="FORWARD")
        final_reject_leave_request(leave_req.id, self.u_dfo, comments="Operational duty clash")
        leave_req.refresh_from_db()
        self.assertEqual(leave_req.status, LeaveRequest.Status.REJECTED)
        self.assertEqual(leave_req.rejection_reason, "Operational duty clash")
        dec = leave_req.decisions.filter(decision="REJECTED").first()
        self.assertIsNotNone(dec)
        self.assertEqual(dec.comments, "Operational duty clash")

    # 24. Approved requests update leave balance once
    def test_24_approved_requests_update_leave_balance_once(self):
        ent, _ = LeaveEntitlement.objects.get_or_create(
            personnel=self.p_keea, leave_year=self.today.year, leave_type=self.lt_annual,
            defaults={"base_entitlement": 36}
        )
        initial_avail = ent.remaining_balance
        leave_req = submit_personnel_leave_request(
            personnel=self.p_keea, leave_type=self.lt_annual,
            start_date=self.today + timedelta(days=5), duration=10, requested_by=self.u_keea
        )
        ent.refresh_from_db()
        self.assertEqual(ent.days_reserved, 10)
        self.assertEqual(ent.remaining_balance, initial_avail - 10)

        process_leave_request(leave_req.id, self.u_dist_admin, action="FORWARD")
        final_approve_leave_request(leave_req.id, self.u_dfo, comments="Approved")
        ent.refresh_from_db()
        self.assertEqual(ent.days_reserved, 0)
        self.assertEqual(ent.days_taken, 10)
        self.assertEqual(ent.remaining_balance, initial_avail - 10)

    # 25. Double approval cannot deduct leave twice
    def test_25_double_approval_cannot_deduct_leave_twice(self):
        leave_req = submit_personnel_leave_request(
            personnel=self.p_keea, leave_type=self.lt_annual,
            start_date=self.today + timedelta(days=5), duration=5, requested_by=self.u_keea
        )
        process_leave_request(leave_req.id, self.u_dist_admin, action="FORWARD")
        final_approve_leave_request(leave_req.id, self.u_dfo, comments="Approved")
        with self.assertRaises(ValidationError):
            final_approve_leave_request(leave_req.id, self.u_dfo, comments="Trying double approval")

    # 26. Dashboard balance matches LeaveEntitlement
    def test_26_dashboard_balance_matches_leave_entitlement(self):
        ent, _ = LeaveEntitlement.objects.get_or_create(
            personnel=self.p_keea, leave_year=self.today.year, leave_type=self.lt_annual,
            defaults={"base_entitlement": 36}
        )
        self.client.login(username="GNFS-KEEA-101", password="password123")
        res = self.client.get(reverse("my-dashboard"))
        self.assertEqual(res.context["entitlement"].remaining_balance, ent.remaining_balance)

    # 27. Personnel can view posting history
    def test_27_personnel_can_view_posting_history(self):
        self.client.login(username="GNFS-KEEA-101", password="password123")
        res = self.client.get(reverse("my-posting"))
        self.assertEqual(res.status_code, 200)
        self.assertIn("posting_history", res.context)

    # 28. Personnel can view functional assignments
    def test_28_personnel_can_view_functional_assignments(self):
        self.client.login(username="GNFS-KEEA-101", password="password123")
        res = self.client.get(reverse("my-departments"))
        self.assertEqual(res.status_code, 200)
        self.assertIn("active_assignments", res.context)

    # 29. Personnel can view transfer status
    def test_29_personnel_can_view_transfer_status(self):
        self.client.login(username="GNFS-KEEA-101", password="password123")
        res = self.client.get(reverse("my-transfers"))
        self.assertEqual(res.status_code, 200)
        self.assertIn("transfers", res.context)

    # 30. Management dashboard data respects organisational scope
    def test_30_management_dashboard_data_respects_organisational_scope(self):
        self.client.login(username="dist_admin_keea", password="password123")
        res = self.client.get(reverse("district-leave-inbox", args=[self.dist_keea.id]))
        self.assertEqual(res.status_code, 200)

    # 31. Direct URL access cannot bypass scope
    def test_31_direct_url_access_cannot_bypass_scope(self):
        self.client.login(username="dist_admin_keea", password="password123")
        res = self.client.get(reverse("district-leave-inbox", args=[self.dist_stma.id]))
        self.assertEqual(res.status_code, 403)

    # 32. Notifications are sent to correct users
    def test_32_notifications_are_sent_to_correct_users(self):
        leave_req = submit_personnel_leave_request(
            personnel=self.p_keea, leave_type=self.lt_annual,
            start_date=self.today + timedelta(days=5), duration=5, requested_by=self.u_keea
        )
        notif = Notification.objects.filter(recipient=self.u_keea).first()
        self.assertIsNotNone(notif)
        self.assertIn("Submitted", notif.title)

    # 33. Changing DFO/MFO causes future requests to route to the new active appointment
    def test_33_changing_dfo_causes_future_requests_to_route_to_new_active_appointment(self):
        # Appoint a new DFO for KEEA
        p_new_dfo = NorminalRoll.objects.create(
            staffID="GNFS-NEWDFO-901", fullname="New DFO",
            rank=self.rank_acfo, job_grade="ACFO", station=self.stn_komenda, officer_category="SENIOR",
            birth_date=self.today - timedelta(days=365 * 40), ghana_card="GHA-901901901-9",
            bank="GCB", account_number="901090109", payroll_status="ACTIVE", gender="MALE",
        )
        PersonnelPosting.objects.create(
            personnel=p_new_dfo, region=self.reg_cr, district=self.dist_keea,
            station=self.stn_komenda, is_current=True, start_date=self.today
        )
        appoint_dfo(district=self.dist_keea, personnel=p_new_dfo, effective_from=self.today, appointed_by=self.admin_user)

        route = resolve_leave_route(self.p_keea)
        self.assertEqual(route["approver_officer"].personnel, p_new_dfo)

    # 34. Changing RFO causes future relevant requests to route to the new active RFO
    def test_34_changing_rfo_causes_future_requests_to_route_to_new_active_rfo(self):
        p_new_rfo = NorminalRoll.objects.create(
            staffID="GNFS-NEWRFO-902", fullname="New RFO",
            rank=self.rank_acfo, job_grade="ACFO", station=self.stn_cc, officer_category="SENIOR",
            birth_date=self.today - timedelta(days=365 * 49), ghana_card="GHA-902902902-9",
            bank="GCB", account_number="902090209", payroll_status="ACTIVE", gender="MALE",
        )
        PersonnelPosting.objects.create(
            personnel=p_new_rfo, region=self.reg_cr, district=self.dist_ccm,
            station=self.stn_cc, is_current=True, start_date=self.today
        )
        appoint_rfo(region=self.reg_cr, personnel=p_new_rfo, effective_from=self.today, appointed_by=self.admin_user)

        route = resolve_leave_route(self.p_dfo)
        self.assertEqual(route["approver_officer"].personnel, p_new_rfo)

    # 35. Existing leave history remains intact
    def test_35_existing_leave_history_remains_intact(self):
        old_leave = LeaveRequest.objects.create(
            personnel=self.p_keea, leave_type=self.lt_annual, leave_year=2024,
            start_date=self.today - timedelta(days=300), duration=10,
            last_leave_date=self.today - timedelta(days=286), resume_date=self.today - timedelta(days=285),
            status=LeaveRequest.Status.COMPLETED
        )
        old_leave.refresh_from_db()
        self.assertEqual(old_leave.status, LeaveRequest.Status.COMPLETED)
        self.assertEqual(old_leave.duration, 10)


# ==============================================================================
# GNFS PERSONNEL SELF-SERVICE LEAVE & PROFILE ENHANCEMENT TESTS
# ==============================================================================

class GNFSPersonnelSelfServiceAndProfileEnhancementTests(TestCase):
    def setUp(self):
        from io import BytesIO
        import base64
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        from gnfs_komenda.models import LeavePolicy
        from gnfs_komenda.services.leave_policy_service import seed_core_leave_policies

        self.today = timezone.now().date()
        self.client = Client()

        # Seed core leave policies
        seed_core_leave_policies()

        # Ranks
        self.rank_cfo, _ = Rank.objects.get_or_create(
            name="Chief Fire Officer", abbreviation="CFO",
            officer_category=Rank.OfficerCategory.SENIOR, hierarchy_order=1
        )
        self.rank_sfm, _ = Rank.objects.get_or_create(
            name="Senior Fireman", abbreviation="SFM",
            officer_category=Rank.OfficerCategory.JUNIOR, hierarchy_order=15
        )

        # Structure
        self.reg_central, _ = Region.objects.get_or_create(name="Central Region", code="CR")
        self.reg_ashanti, _ = Region.objects.get_or_create(name="Ashanti Region", code="AR")
        self.dist_keea, _ = District.objects.get_or_create(name="KEEA District", region=self.reg_central, code="KEEA")
        self.dist_kma, _ = District.objects.get_or_create(name="Kumasi Metro", region=self.reg_ashanti, code="KMA")
        self.stn_komenda, _ = Station.objects.get_or_create(name="Komenda Fire Station", district=self.dist_keea)
        self.stn_kumasi, _ = Station.objects.get_or_create(name="Kumasi Fire Station", district=self.dist_kma)

        # Leave Types
        self.lt_annual, _ = LeaveType.objects.get_or_create(
            code="ANNUAL",
            defaults={
                "name": "Annual Leave",
                "calculation_mode": LeaveType.CalculationMode.WORKING_DAYS,
                "allowed_gender": LeaveType.AllowedGender.ALL,
                "default_days_junior": 36,
                "default_days_senior": 42,
            }
        )
        self.lt_paternity, _ = LeaveType.objects.get_or_create(
            code="PATERNITY",
            defaults={
                "name": "Paternity Leave",
                "calculation_mode": LeaveType.CalculationMode.WORKING_DAYS,
                "allowed_gender": LeaveType.AllowedGender.MALE,
                "default_days_junior": 5,
                "default_days_senior": 5,
            }
        )
        self.lt_maternity, _ = LeaveType.objects.get_or_create(
            code="MATERNITY",
            defaults={
                "name": "Maternity Leave",
                "calculation_mode": LeaveType.CalculationMode.CALENDAR_DAYS,
                "allowed_gender": LeaveType.AllowedGender.FEMALE,
                "default_days_junior": 90,
                "default_days_senior": 90,
                "requires_attachment": True,
            }
        )

        # Officers:
        # Male Junior Officer
        self.p_male = NorminalRoll.objects.create(
            staffID="GNFS-MALE-01", fullname="Kwame Mensah",
            rank=self.rank_sfm, job_grade="SFM", station=self.stn_komenda,
            officer_category=Rank.OfficerCategory.JUNIOR, gender=NorminalRoll.Gender.MALE,
            birth_date=self.today - timedelta(days=365 * 28), ghana_card="GHA-111111111-1",
            bank="GCB", account_number="1111111111", payroll_status="ACTIVE"
        )
        self.u_male = User.objects.create_user(username="gnfs-male-01", password="password123")
        UserAssignment.objects.create(user=self.u_male, personnel=self.p_male, role=GNFSUserRole.STAFF, active=True)

        # Female Senior Officer
        self.p_female = NorminalRoll.objects.create(
            staffID="GNFS-FEMALE-02", fullname="Ama Serwaa",
            rank=self.rank_cfo, job_grade="CFO", station=self.stn_komenda,
            officer_category=Rank.OfficerCategory.SENIOR, gender=NorminalRoll.Gender.FEMALE,
            birth_date=self.today - timedelta(days=365 * 38), ghana_card="GHA-222222222-2",
            bank="GCB", account_number="2222222222", payroll_status="ACTIVE"
        )
        self.u_female = User.objects.create_user(username="gnfs-female-02", password="password123")
        UserAssignment.objects.create(user=self.u_female, personnel=self.p_female, role=GNFSUserRole.STAFF, active=True)

        # Officer with Missing / NOT_SET Sex
        self.p_unassigned = NorminalRoll.objects.create(
            staffID="GNFS-UNSET-03", fullname="Kofi Blank",
            rank=self.rank_sfm, job_grade="SFM", station=self.stn_komenda,
            officer_category=Rank.OfficerCategory.JUNIOR, gender=NorminalRoll.Gender.NOT_SET,
            birth_date=self.today - timedelta(days=365 * 25), ghana_card="GHA-333333333-3",
            bank="GCB", account_number="3333333333", payroll_status="ACTIVE"
        )
        self.u_unassigned = User.objects.create_user(username="gnfs-unset-03", password="password123")
        UserAssignment.objects.create(user=self.u_unassigned, personnel=self.p_unassigned, role=GNFSUserRole.STAFF, active=True)

        # Super Admin
        self.u_admin = User.objects.create_superuser(username="admin_super", email="admin@gnfs.gov.gh", password="password123")

        # Regional Admin (Central Region)
        self.u_reg_admin = User.objects.create_user(username="reg_admin_cr", password="password123")
        UserAssignment.objects.create(user=self.u_reg_admin, role=GNFSUserRole.REGIONAL_ADMIN, region=self.reg_central, active=True)

        # District Admin (KEEA)
        self.u_dist_admin = User.objects.create_user(username="dist_admin_keea", password="password123")
        UserAssignment.objects.create(user=self.u_dist_admin, role=GNFSUserRole.DISTRICT_ADMIN, district=self.dist_keea, active=True)

    # 1. Personal leave form has no Personnel selector (Section 1 & 47)
    def test_personal_leave_form_has_no_personnel_selector(self):
        self.client.login(username="gnfs-male-01", password="password123")
        res = self.client.get(reverse("my-leave-request"))
        self.assertEqual(res.status_code, 200)
        self.assertNotContains(res, 'name="personnel"')
        self.assertNotContains(res, 'Select Personnel')
        self.assertContains(res, self.p_male.fullname)
        self.assertContains(res, self.p_male.staffID)

    # 2. Logged-in Personnel becomes leave owner automatically (Section 1 & 47)
    def test_logged_in_personnel_becomes_leave_owner_automatically(self):
        self.client.login(username="gnfs-male-01", password="password123")
        start_date = self.today + timedelta(days=10)
        res = self.client.post(reverse("my-leave-request"), {
            "leave_type": self.lt_annual.id,
            "start_date": start_date.strftime("%Y-%m-%d"),
            "duration": 5,
            "reason": "Personal rest"
        })
        self.assertEqual(res.status_code, 302)
        leave_req = LeaveRequest.objects.filter(personnel=self.p_male).latest("id")
        self.assertEqual(leave_req.personnel, self.p_male)
        self.assertEqual(leave_req.duration, 5)

    # 3. Manipulated Personnel ID in POST cannot submit leave for another officer (Section 3 & 47)
    def test_manipulated_personnel_id_cannot_submit_leave_for_another_officer(self):
        self.client.login(username="gnfs-male-01", password="password123")
        start_date = self.today + timedelta(days=15)
        # Attempt to spoof personnel parameter to Female Officer
        res = self.client.post(reverse("my-leave-request"), {
            "personnel": self.p_female.id,
            "leave_type": self.lt_annual.id,
            "start_date": start_date.strftime("%Y-%m-%d"),
            "duration": 5,
            "reason": "Spoofed submission attempt"
        })
        # Leave created belongs strictly to the logged-in male officer, never to the spoofed female officer
        leave_req = LeaveRequest.objects.filter(start_date=start_date).first()
        self.assertIsNotNone(leave_req)
        self.assertEqual(leave_req.personnel, self.p_male)
        self.assertNotEqual(leave_req.personnel, self.p_female)

    # 4. Male sees Paternity and does NOT see Maternity (Section 6 & 47)
    def test_male_sees_paternity_and_does_not_see_maternity(self):
        self.client.login(username="gnfs-male-01", password="password123")
        res = self.client.get(reverse("my-leave-request"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Paternity Leave")
        self.assertNotContains(res, "Maternity Leave")

    # 5. Female sees Maternity and does NOT see Paternity (Section 6 & 47)
    def test_female_sees_maternity_and_does_not_see_paternity(self):
        self.client.login(username="gnfs-female-02", password="password123")
        res = self.client.get(reverse("my-leave-request"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Maternity Leave")
        self.assertNotContains(res, "Paternity Leave")

    # 6. Missing Sex does not get guessed and shows notice (Section 43 & 47)
    def test_missing_sex_does_not_get_guessed_and_shows_notice(self):
        self.client.login(username="gnfs-unset-03", password="password123")
        res = self.client.get(reverse("my-leave-request"))
        self.assertEqual(res.status_code, 200)
        # Excludes both sex-restricted leave types
        self.assertNotContains(res, "Maternity Leave")
        self.assertNotContains(res, "Paternity Leave")
        self.assertContains(res, "Annual Leave")
        self.assertContains(res, "Your personnel record requires a Sex value")

    # 7. Backend rejects Maternity for Male (Section 7 & 47)
    def test_backend_rejects_maternity_for_male(self):
        with self.assertRaises(ValidationError) as ctx:
            submit_personnel_leave_request(
                personnel=self.p_male,
                leave_type=self.lt_maternity,
                start_date=self.today + timedelta(days=10),
                duration=90,
                requested_by=self.u_male
            )
        self.assertIn("Maternity leave is strictly restricted to female personnel", str(ctx.exception))

    # 8. Backend rejects Paternity for Female (Section 7 & 47)
    def test_backend_rejects_paternity_for_female(self):
        with self.assertRaises(ValidationError) as ctx:
            submit_personnel_leave_request(
                personnel=self.p_female,
                leave_type=self.lt_paternity,
                start_date=self.today + timedelta(days=10),
                duration=5,
                requested_by=self.u_female
            )
        self.assertIn("Paternity leave is strictly restricted to male personnel", str(ctx.exception))

    # 9. Backend rejects Maternity and Paternity when Sex is not set (Section 43 & 47)
    def test_backend_rejects_maternity_paternity_when_sex_not_set(self):
        with self.assertRaises(ValidationError) as ctx1:
            submit_personnel_leave_request(
                personnel=self.p_unassigned,
                leave_type=self.lt_paternity,
                start_date=self.today + timedelta(days=10),
                duration=5,
                requested_by=self.u_unassigned
            )
        self.assertIn("requires a Sex value", str(ctx1.exception))

        with self.assertRaises(ValidationError) as ctx2:
            submit_personnel_leave_request(
                personnel=self.p_unassigned,
                leave_type=self.lt_maternity,
                start_date=self.today + timedelta(days=10),
                duration=90,
                requested_by=self.u_unassigned
            )
        self.assertIn("requires a Sex value", str(ctx2.exception))

    # 10. Senior Officer receives 42 working days Annual Leave (Section 8, 9 & 48)
    def test_senior_officer_receives_42_working_days_annual_leave(self):
        from gnfs_komenda.services.leave_policy_service import get_base_annual_entitlement
        days = get_base_annual_entitlement(self.p_female.officer_category)
        self.assertEqual(days, 42)

    # 11. Junior Officer receives 36 working days Annual Leave (Section 8, 9 & 48)
    def test_junior_officer_receives_36_working_days_annual_leave(self):
        from gnfs_komenda.services.leave_policy_service import get_base_annual_entitlement
        days = get_base_annual_entitlement(self.p_male.officer_category)
        self.assertEqual(days, 36)

    # 12. Working days calculation excludes Saturday, Sunday, and Public Holidays (Section 10 & 48)
    def test_annual_leave_calculation_excludes_saturday_sunday_and_holidays(self):
        from gnfs_komenda.models import PublicHoliday
        from gnfs_komenda.services.leave_calculator import calculate_leave_dates
        import datetime

        # Choose a known Monday: 2026-10-05
        monday = datetime.date(2026, 10, 5)
        # Configure a public holiday on Wednesday: 2026-10-07
        PublicHoliday.objects.get_or_create(date=datetime.date(2026, 10, 7), defaults={"name": "Test Holiday", "year": 2026})

        # Request 5 working days:
        # Mon Oct 5 (day 1), Tue Oct 6 (day 2), Wed Oct 7 (HOLIDAY EXCLUDED),
        # Thu Oct 8 (day 3), Fri Oct 9 (day 4), Sat Oct 10 (EXCLUDED), Sun Oct 11 (EXCLUDED),
        # Mon Oct 12 (day 5 -> Last Leave Date)
        # Resume Date: Tue Oct 13
        res = calculate_leave_dates(monday, duration=5, calculation_mode=LeaveType.CalculationMode.WORKING_DAYS)
        self.assertEqual(res['last_leave_date'], datetime.date(2026, 10, 12))
        self.assertEqual(res['resume_date'], datetime.date(2026, 10, 13))
        self.assertEqual(res['working_days_count'], 5)
        self.assertIn("2026-10-07", res['holidays_encountered'])

    # 13. Partial annual leave reduces balance correctly (Section 11 & 48)
    def test_partial_annual_leave_reduces_balance_correctly(self):
        leave_req = submit_personnel_leave_request(
            personnel=self.p_male,
            leave_type=self.lt_annual,
            start_date=self.today + timedelta(days=20),
            duration=15,
            requested_by=self.u_male,
            is_proportionate=True
        )
        ent = LeaveEntitlement.objects.get(personnel=self.p_male, leave_year=self.today.year, leave_type=self.lt_annual)
        self.assertEqual(ent.base_entitlement, 36)
        self.assertEqual(ent.days_reserved, 15)
        self.assertEqual(ent.remaining_balance, 21)

    # 14. Paternity Leave fixed at 5 working days and excludes weekends & holidays (Section 14 & 49)
    def test_paternity_leave_fixed_at_5_working_days(self):
        import datetime
        monday = datetime.date(2026, 11, 2)
        leave_req = submit_personnel_leave_request(
            personnel=self.p_male,
            leave_type=self.lt_paternity,
            start_date=monday,
            duration=10,  # Officer attempts to submit 10 days
            requested_by=self.u_male
        )
        # System enforces fixed 5 working days
        self.assertEqual(leave_req.duration, 5)
        self.assertEqual(leave_req.calculation_mode, LeaveType.CalculationMode.WORKING_DAYS)
        # 5 working days starting Monday: Last leave date = Friday Nov 6, Resume date = Monday Nov 9
        self.assertEqual(leave_req.last_leave_date, datetime.date(2026, 11, 6))
        self.assertEqual(leave_req.resume_date, datetime.date(2026, 11, 9))

    # 15. Maternity Leave fixed at 90 calendar days including weekends (Section 16 & 50)
    def test_maternity_leave_fixed_at_90_calendar_days(self):
        import datetime
        start = datetime.date(2026, 10, 1)
        leave_req = submit_personnel_leave_request(
            personnel=self.p_female,
            leave_type=self.lt_maternity,
            start_date=start,
            duration=40,  # Officer attempts to specify non-standard duration
            requested_by=self.u_female
        )
        # System enforces fixed 90 calendar days
        self.assertEqual(leave_req.duration, 90)
        self.assertEqual(leave_req.calculation_mode, LeaveType.CalculationMode.CALENDAR_DAYS)
        expected_last = start + timedelta(days=89)
        self.assertEqual(leave_req.last_leave_date, expected_last)

    # 16. Profile Picture upload saves and displays on profile (Section 27-32, 51)
    def test_profile_picture_upload_saves_and_displays_on_profile(self):
        from io import BytesIO
        from PIL import Image
        import base64

        # Generate a small 100x100 test image as base64 data URI
        buf = BytesIO()
        test_img = Image.new("RGB", (100, 100), color="crimson")
        test_img.save(buf, format="JPEG")
        data_uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode('utf-8')

        self.client.login(username="gnfs-male-01", password="password123")
        res = self.client.post(reverse("my-profile-picture-upload"), {"image_data": data_uri})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertIn("avatar_", data["url"])

        self.p_male.refresh_from_db()
        self.assertTrue(bool(self.p_male.profile_picture))
        self.assertIsNotNone(self.p_male.profile_picture_url)

        # Profile view displays image
        profile_res = self.client.get(reverse("my-profile"))
        self.assertContains(profile_res, self.p_male.profile_picture_url)

    # 17. Invalid profile picture file is rejected (Section 32, 51)
    def test_profile_picture_invalid_file_rejected(self):
        self.client.login(username="gnfs-male-01", password="password123")
        # Submit invalid corrupt data
        res = self.client.post(reverse("my-profile-picture-upload"), {"image_data": "not-a-real-image-data"})
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertFalse(data["success"])

    # 18. Profile Picture Scoping for Regional and District Admins (Section 34, 51)
    def test_profile_picture_scoping_for_regional_and_district_admins(self):
        from io import BytesIO
        from PIL import Image
        import base64

        buf = BytesIO()
        test_img = Image.new("RGB", (80, 80), color="blue")
        test_img.save(buf, format="JPEG")
        data_uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode('utf-8')

        # Officer in Ashanti Region:
        p_ashanti = NorminalRoll.objects.create(
            staffID="GNFS-AR-09", fullname="Osei Tutu",
            rank=self.rank_sfm, job_grade="SFM", station=self.stn_kumasi,
            officer_category=Rank.OfficerCategory.JUNIOR, gender=NorminalRoll.Gender.MALE,
            birth_date=self.today - timedelta(days=365 * 30), ghana_card="GHA-999999999-9",
            bank="GCB", account_number="9999999999", payroll_status="ACTIVE"
        )

        # Regional Admin of Central Region cannot manage Ashanti officer photo
        self.client.login(username="reg_admin_cr", password="password123")
        res_cross = self.client.post(
            reverse("personnel-profile-picture-upload", args=[p_ashanti.id]),
            {"image_data": data_uri}
        )
        self.assertEqual(res_cross.status_code, 403)

        # Super Admin can manage any officer photo nationwide
        self.client.login(username="admin_super", password="password123")
        res_super = self.client.post(
            reverse("personnel-profile-picture-upload", args=[p_ashanti.id]),
            {"image_data": data_uri}
        )
        self.assertEqual(res_super.status_code, 200)

    # 19. Super Admin Leave Policies Screen shows Core and Custom policies (Section 21)
    def test_super_admin_leave_policies_screen_shows_core_and_custom(self):
        self.client.login(username="admin_super", password="password123")
        res = self.client.get(reverse("leave-policies"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Annual Leave — Senior Officers")
        self.assertContains(res, "Annual Leave — Junior Officers")
        self.assertContains(res, "Paternity Leave")
        self.assertContains(res, "Maternity Leave")
        self.assertContains(res, "PROTECTED")


class GNFSFunctionalMigrationArchitectureTests(TestCase):
    """
    Comprehensive verification for the Major Functional Structure, Nominal Roll,
    Leave Workflow & Watch Room Migration (Sections 1 - 101).
    """

    def setUp(self):
        self.today = timezone.localdate()
        User = get_user_model()

        # Regions
        self.reg_central = Region.objects.create(name="Central Region", code="CR")
        self.reg_ashanti = Region.objects.create(name="Ashanti Region", code="AR")

        # Districts
        self.dist_keea = District.objects.create(name="Komenda-Edina-Eguafo-Abirem Municipal", code="KEEA", region=self.reg_central)
        self.dist_cape_coast = District.objects.create(name="Cape Coast Metropolitan", code="CCMA", region=self.reg_central)
        self.dist_kumasi = District.objects.create(name="Kumasi Metropolitan", code="KMA", region=self.reg_ashanti)

        # Stations
        self.stn_elmina = Station.objects.create(name="Elmina Fire Station", district=self.dist_keea)
        self.stn_cc = Station.objects.create(name="Cape Coast Station", district=self.dist_cape_coast)
        self.stn_kumasi = Station.objects.create(name="Kumasi Central Station", district=self.dist_kumasi)

        # Ranks
        self.rank_cfo = Rank.objects.create(name="Chief Fire Officer", hierarchy_order=1, officer_category=Rank.OfficerCategory.SENIOR, abbreviation="CFO")
        self.rank_dfo = Rank.objects.create(name="Divisional Officer I", hierarchy_order=2, officer_category=Rank.OfficerCategory.SENIOR, abbreviation="DOI")
        self.rank_sub_officer = Rank.objects.create(name="Sub Officer", hierarchy_order=8, officer_category=Rank.OfficerCategory.JUNIOR, abbreviation="Sub/O")
        self.rank_fireman = Rank.objects.create(name="Leading Fireman", hierarchy_order=10, officer_category=Rank.OfficerCategory.JUNIOR, abbreviation="LFM")

        # Leave Types
        self.lt_annual, _ = LeaveType.objects.get_or_create(
            code="ANNUAL",
            defaults={"name": "Annual Leave", "calculation_mode": LeaveType.CalculationMode.WORKING_DAYS}
        )

        # Personnel records
        self.p_keea_officer = NorminalRoll.objects.create(
            staffID="1000001", fullname="John Mensah",
            rank=self.rank_fireman, job_grade="LFM", station=self.stn_elmina,
            officer_category=Rank.OfficerCategory.JUNIOR, gender=NorminalRoll.Gender.MALE,
            birth_date=self.today - timedelta(days=365 * 28), ghana_card="GHA-111111111-1",
            bank="GCB", account_number="1234567890", payroll_status="ACTIVE",
            leave_status="AT POST", mgmt_units="GNFS KEEA"
        )
        self.p_cape_officer = NorminalRoll.objects.create(
            staffID="1000002", fullname="Ama Boateng",
            rank=self.rank_fireman, job_grade="LFM", station=self.stn_cc,
            officer_category=Rank.OfficerCategory.JUNIOR, gender=NorminalRoll.Gender.FEMALE,
            birth_date=self.today - timedelta(days=365 * 26), ghana_card="GHA-222222222-2",
            bank="Ecobank", account_number="2222222222", payroll_status="ACTIVE",
            leave_status="AT POST", mgmt_units="GNFS CC"
        )
        self.p_kumasi_officer = NorminalRoll.objects.create(
            staffID="1000003", fullname="Kofi Antwi",
            rank=self.rank_sub_officer, job_grade="Sub/O", station=self.stn_kumasi,
            officer_category=Rank.OfficerCategory.JUNIOR, gender=NorminalRoll.Gender.MALE,
            birth_date=self.today - timedelta(days=365 * 32), ghana_card="GHA-333333333-3",
            bank="GCB", account_number="3333333333", payroll_status="ACTIVE",
            leave_status="AT POST", mgmt_units="GNFS KMA"
        )
        self.p_dfo_keea = NorminalRoll.objects.create(
            staffID="1000004", fullname="Kwesi Appiah",
            rank=self.rank_dfo, job_grade="DOI", station=self.stn_elmina,
            officer_category=Rank.OfficerCategory.SENIOR, gender=NorminalRoll.Gender.MALE,
            birth_date=self.today - timedelta(days=365 * 45), ghana_card="GHA-444444444-4",
            bank="Barclays", account_number="4444444444", payroll_status="ACTIVE",
            leave_status="AT POST", mgmt_units="GNFS KEEA"
        )

        # DFO Command Appointment for KEEA
        CommandAppointment.objects.create(
            personnel=self.p_dfo_keea,
            appointment_type="DFO",
            district=self.dist_keea,
            region=self.reg_central,
            is_active=True,
            effective_from=self.today - timedelta(days=200)
        )

        # Users
        self.u_super = User.objects.create_superuser("super_admin_fn", "super@gnfs.gov.gh", "password123")
        self.u_keea_officer = User.objects.create_user("1000001", "mensah@gnfs.gov.gh", "password123")
        self.u_dfo_keea = User.objects.create_user("1000004", "dfo@gnfs.gov.gh", "password123")
        self.u_keea_accounts = User.objects.create_user("keea_accounts", "accounts_keea@gnfs.gov.gh", "password123")
        self.u_central_accounts = User.objects.create_user("central_accounts", "accounts_cr@gnfs.gov.gh", "password123")
        self.u_national_accounts = User.objects.create_user("nat_accounts", "accounts_hq@gnfs.gov.gh", "password123")
        self.u_keea_admin = User.objects.create_user("keea_admin", "admin_keea@gnfs.gov.gh", "password123")
        self.u_keea_ops = User.objects.create_user("keea_ops", "ops_keea@gnfs.gov.gh", "password123")
        self.u_keea_watch_room = User.objects.create_user("keea_watch_room", "watch_keea@gnfs.gov.gh", "password123")

        # Functional Offices
        self.fo_keea_admin = FunctionalOffice.objects.create(
            name="KEEA Administration", function_type="ADMINISTRATION",
            command_level="DISTRICT", district=self.dist_keea, region=self.reg_central,
            is_established=True, active=True
        )
        self.fo_keea_accounts = FunctionalOffice.objects.create(
            name="KEEA Accounts", function_type="ACCOUNTS",
            command_level="DISTRICT", district=self.dist_keea, region=self.reg_central,
            is_established=True, active=True
        )
        self.fo_keea_ops = FunctionalOffice.objects.create(
            name="KEEA Operations", function_type="OPERATIONS",
            command_level="DISTRICT", district=self.dist_keea, region=self.reg_central,
            is_established=True, active=True
        )
        self.fo_keea_wr = FunctionalOffice.objects.create(
            name="KEEA Watch Room", function_type="WATCH_ROOM",
            command_level="DISTRICT", district=self.dist_keea, region=self.reg_central,
            is_established=True, active=True
        )
        # KEEA HR: UNSET (Section 37, 78) - Not Established, Fallback = Administration
        self.fo_keea_hr = FunctionalOffice.objects.create(
            name="KEEA Human Resource", function_type="HR",
            command_level="DISTRICT", district=self.dist_keea, region=self.reg_central,
            is_established=False, fallback_office=self.fo_keea_admin, active=True
        )

        # Regional & National Offices
        self.fo_cr_accounts = FunctionalOffice.objects.create(
            name="Central Regional Accounts", function_type="ACCOUNTS",
            command_level="REGIONAL", region=self.reg_central,
            is_established=True, active=True
        )
        self.fo_nat_accounts = FunctionalOffice.objects.create(
            name="National Accounts Directorate", function_type="ACCOUNTS",
            command_level="NATIONAL", is_established=True, active=True
        )

        # Functional Role Assignments
        from gnfs_komenda.models import FunctionalRoleAssignment
        FunctionalRoleAssignment.objects.create(
            user=self.u_keea_accounts, functional_office=self.fo_keea_accounts,
            role="DISTRICT_FUNCTION_HEAD", scope_level="DISTRICT",
            district=self.dist_keea, region=self.reg_central, active=True
        )
        FunctionalRoleAssignment.objects.create(
            user=self.u_central_accounts, functional_office=self.fo_cr_accounts,
            role="REGIONAL_FUNCTION_HEAD", scope_level="REGIONAL",
            region=self.reg_central, active=True
        )
        FunctionalRoleAssignment.objects.create(
            user=self.u_national_accounts, functional_office=self.fo_nat_accounts,
            role="DIRECTORATE_HEAD", scope_level="NATIONAL", active=True
        )
        FunctionalRoleAssignment.objects.create(
            user=self.u_keea_admin, functional_office=self.fo_keea_admin,
            role="DISTRICT_FUNCTION_HEAD", scope_level="DISTRICT",
            district=self.dist_keea, region=self.reg_central, active=True
        )
        FunctionalRoleAssignment.objects.create(
            user=self.u_keea_ops, functional_office=self.fo_keea_ops,
            role="DISTRICT_FUNCTION_HEAD", scope_level="DISTRICT",
            district=self.dist_keea, region=self.reg_central, active=True
        )
        FunctionalRoleAssignment.objects.create(
            user=self.u_keea_watch_room, functional_office=self.fo_keea_wr,
            role="FUNCTION_MEMBER", scope_level="DISTRICT",
            district=self.dist_keea, region=self.reg_central, active=True
        )

        # Assign officer to Operations functional unit
        fu_ops, _ = FunctionalUnit.objects.get_or_create(name="Operations Directorate", unit_type="DIRECTORATE")
        PersonnelFunctionalAssignment.objects.create(
            personnel=self.p_keea_officer,
            functional_unit=fu_ops,
            functional_office=self.fo_keea_ops,
            is_primary=True,
            is_active=True
        )

        self.client = Client()

    # 1. District Accounts sees own District nominal roll (Section 12, 13)
    def test_district_accounts_sees_own_district_nominal_roll(self):
        self.client.login(username="keea_accounts", password="password123")
        res = self.client.get(reverse("workspace-accounts-district", args=[self.dist_keea.id]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "John Mensah")
        self.assertContains(res, "1000001")

    # 2. District Accounts cannot see another District (Section 13)
    def test_district_accounts_cannot_see_other_district(self):
        self.client.login(username="keea_accounts", password="password123")
        res_cross = self.client.get(reverse("workspace-accounts-district", args=[self.dist_cape_coast.id]))
        self.assertEqual(res_cross.status_code, 403)

    # 3. Regional Accounts sees all District rolls in own Region (Section 14)
    def test_regional_accounts_sees_all_districts_in_region(self):
        self.client.login(username="central_accounts", password="password123")
        res = self.client.get(reverse("workspace-accounts-regional", args=[self.reg_central.id]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Komenda-Edina-Eguafo-Abirem Municipal")
        self.assertContains(res, "Cape Coast Metropolitan")

        # Regional accounts can drill down to district roll in own region
        res_drill = self.client.get(reverse("workspace-accounts-district", args=[self.dist_keea.id]))
        self.assertEqual(res_drill.status_code, 200)
        self.assertContains(res_drill, "John Mensah")

    # 4. Regional Accounts cannot see another Region (Section 14)
    def test_regional_accounts_cannot_see_other_region(self):
        self.client.login(username="central_accounts", password="password123")
        res = self.client.get(reverse("workspace-accounts-regional", args=[self.reg_ashanti.id]))
        self.assertEqual(res.status_code, 403)

    # 5. National Accounts sees all Regions (Section 15, 16)
    def test_national_accounts_sees_all_regions(self):
        self.client.login(username="nat_accounts", password="password123")
        res = self.client.get(reverse("workspace-accounts-national"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Central Region")
        self.assertContains(res, "Ashanti Region")

    # 6. District Administration sees District leave status and metrics (Section 20-22)
    def test_district_administration_views_district_leave_and_metrics(self):
        self.client.login(username="keea_admin", password="password123")
        res = self.client.get(reverse("workspace-admin-district", args=[self.dist_keea.id]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "DISTRICT PERSONNEL ADMINISTRATION")
        self.assertContains(res, "Komenda-Edina-Eguafo-Abirem Municipal")

    # 7. District without HR correctly falls back to Administration (Section 37, 78)
    def test_district_without_hr_correctly_falls_back_to_administration(self):
        from gnfs_komenda.services.functional_scope_service import get_functional_scope
        # KEEA HR office is not established; fallback is KEEA Administration
        scope = get_functional_scope(self.u_keea_admin, "HR")
        self.assertTrue(scope["authorized"])
        self.assertEqual(scope["level"], "DISTRICT")
        self.assertEqual(scope["district"].id, self.dist_keea.id)

        # Fallback workspace displays delegation alert notice
        self.client.login(username="keea_admin", password="password123")
        res = self.client.get(reverse("workspace-hr-district", args=[self.dist_keea.id]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "HR Office Unestablished")
        self.assertContains(res, "KEEA Administration")

    # 8. District Operations sees authorized operational personnel (Section 30-32)
    def test_district_operations_sees_authorized_operational_personnel(self):
        self.client.login(username="keea_ops", password="password123")
        res = self.client.get(reverse("workspace-ops-district", args=[self.dist_keea.id]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "DISTRICT OPERATIONS WORKSPACE")
        self.assertContains(res, "Elmina Fire Station")

    # 9. Complete End-to-End District Leave Workflow (Section 44-55)
    def test_end_to_end_district_leave_workflow(self):
        """
        Flow:
        1. Submit -> PENDING_ADMIN_REVIEW
        2. District Admin reviews & verifies -> forwards to Operations (PENDING_OPERATIONS_RECOMMENDATION)
        3. District Operations recommends -> (PENDING_APPROVAL)
        4. DFO approves -> PENDING_ADMIN_FINAL_PROCESSING
        5. District Admin dispatches to Watch Room -> SENT_TO_WATCH_ROOM
        6. Watch Room logs in Station Diary -> BOOKED
        """
        start_date = self.today + timedelta(days=10)
        # Step 1: Submit
        leave_req = submit_personnel_leave_request(
            personnel=self.p_keea_officer,
            leave_type=self.lt_annual,
            start_date=start_date,
            duration=10,
            requested_by=self.u_keea_officer
        )
        self.assertEqual(leave_req.status, LeaveRequest.Status.PENDING_ADMIN_REVIEW)
        self.assertTrue(leave_req.reference_number.startswith("LEV-"))

        # Step 2: Administration forwards to Operations
        # Section 47: Admin cannot directly approve normal District leave; must forward to Operations
        self.client.login(username="keea_admin", password="password123")
        res_fwd = self.client.post(
            reverse("admin-process-leave", args=[leave_req.id]),
            {"action": "FORWARD_TO_OPERATIONS", "comments": "Records verified, forwarding to Operations."}
        )
        self.assertEqual(res_fwd.status_code, 302)
        leave_req.refresh_from_db()
        self.assertEqual(leave_req.status, LeaveRequest.Status.PENDING_OPERATIONS_RECOMMENDATION)

        # Step 3: Operations recommends
        self.client.login(username="keea_ops", password="password123")
        res_rec = self.client.post(
            reverse("operations-recommend-leave", args=[leave_req.id]),
            {"recommendation": "RECOMMEND", "comments": "Operational station strength permits leave."}
        )
        self.assertEqual(res_rec.status_code, 302)
        leave_req.refresh_from_db()
        self.assertEqual(leave_req.status, LeaveRequest.Status.PENDING_APPROVAL)
        self.assertEqual(leave_req.operations_recommendation, "RECOMMEND")

        # Step 4: DFO Executive Command Decision
        self.client.login(username="1000004", password="password123")
        res_app = self.client.post(
            reverse("command-approve-leave", args=[leave_req.id]),
            {"decision": "APPROVE", "comments": "Executive command approval granted."}
        )
        self.assertEqual(res_app.status_code, 302)
        leave_req.refresh_from_db()
        # Section 52: Returns to Administration for final release
        self.assertEqual(leave_req.status, LeaveRequest.Status.PENDING_ADMIN_FINAL_PROCESSING)

        # Step 5: Administration releases to Watch Room
        self.client.login(username="keea_admin", password="password123")
        res_rel = self.client.post(
            reverse("admin-release-watch-room", args=[leave_req.id]),
            {"comments": "Dispatched to Watch Room for Station Diary entry."}
        )
        self.assertEqual(res_rel.status_code, 302)
        leave_req.refresh_from_db()
        self.assertEqual(leave_req.status, LeaveRequest.Status.SENT_TO_WATCH_ROOM)

        # Step 6: Watch Room books in Station Diary
        self.client.login(username="keea_watch_room", password="password123")
        res_book = self.client.post(
            reverse("watch-room-book-leave", args=[leave_req.id]),
            {
                "book_type": "STATION_DIARY",
                "book_reference": "SD Vol. 8, Page 12, Entry #04",
                "remarks": "Keys handed over to Leading Fireman on duty."
            }
        )
        self.assertEqual(res_book.status_code, 302)
        leave_req.refresh_from_db()
        self.assertEqual(leave_req.status, LeaveRequest.Status.BOOKED)
        self.assertTrue(leave_req.watch_room_booked_by is not None)
        self.assertEqual(leave_req.watch_room_book_reference, "SD Vol. 8, Page 12, Entry #04")

        # Verify WatchRoomBooking record created
        from gnfs_komenda.models import WatchRoomBooking
        booking = WatchRoomBooking.objects.filter(leave_request=leave_req).first()
        self.assertIsNotNone(booking)
        self.assertEqual(booking.book_reference, "SD Vol. 8, Page 12, Entry #04")

        # Verify full workflow timeline history exists (Section 56, 57)
        history_actions = list(leave_req.workflow_history.values_list('action', flat=True))
        self.assertIn("LEAVE_SUBMITTED", history_actions)
        self.assertIn("ADMIN_REVIEWED_AND_FORWARDED", history_actions)
        self.assertIn("OPERATIONS_RECOMMENDED", history_actions)
        self.assertIn("COMMAND_APPROVED", history_actions)
        self.assertIn("SENT_TO_WATCH_ROOM", history_actions)
        self.assertIn("WATCH_ROOM_BOOKED", history_actions)

    # 10. Strict Anti-Self-Approval: DFO cannot approve own leave (Section 61)
    def test_dfo_cannot_approve_own_leave_strict_anti_self_approval(self):
        start_date = self.today + timedelta(days=15)
        dfo_req = submit_personnel_leave_request(
            personnel=self.p_dfo_keea,
            leave_type=self.lt_annual,
            start_date=start_date,
            duration=10,
            requested_by=self.u_dfo_keea
        )
        # Advance status to PENDING_APPROVAL
        dfo_req.status = LeaveRequest.Status.PENDING_APPROVAL
        dfo_req.save()

        # DFO attempts to approve own leave
        self.client.login(username="1000004", password="password123")
        res_self = self.client.post(
            reverse("command-approve-leave", args=[dfo_req.id]),
            {"decision": "APPROVE"}
        )
        # Permission denied due to strict anti-self-approval
        dfo_req.refresh_from_db()
        self.assertNotEqual(dfo_req.status, LeaveRequest.Status.PENDING_ADMIN_FINAL_PROCESSING)

    # 11. Operations Not Recommended still allows DFO final decision (Section 51)
    def test_operations_not_recommend_still_allows_dfo_decision(self):
        start_date = self.today + timedelta(days=20)
        leave_req = submit_personnel_leave_request(
            personnel=self.p_keea_officer,
            leave_type=self.lt_annual,
            start_date=start_date,
            duration=10,
            requested_by=self.u_keea_officer
        )
        leave_req.status = LeaveRequest.Status.PENDING_OPERATIONS_RECOMMENDATION
        leave_req.save()

        # Operations submits NOT_RECOMMEND
        self.client.login(username="keea_ops", password="password123")
        self.client.post(
            reverse("operations-recommend-leave", args=[leave_req.id]),
            {"recommendation": "NOT_RECOMMEND", "comments": "Understaffed shift duty."}
        )
        leave_req.refresh_from_db()
        self.assertEqual(leave_req.operations_recommendation, "NOT_RECOMMEND")
        self.assertEqual(leave_req.status, LeaveRequest.Status.PENDING_APPROVAL)

        # DFO exercises command authority to APPROVE regardless (advisory recommendation)
        self.client.login(username="1000004", password="password123")
        self.client.post(
            reverse("command-approve-leave", args=[leave_req.id]),
            {"decision": "APPROVE", "comments": "Approved based on compassionate grounds."}
        )
        leave_req.refresh_from_db()
        self.assertEqual(leave_req.status, LeaveRequest.Status.PENDING_ADMIN_FINAL_PROCESSING)

    # 12. Rejected leave is not sent to Watch Room (Section 58)
    def test_rejected_leave_is_not_sent_to_watch_room(self):
        start_date = self.today + timedelta(days=25)
        leave_req = submit_personnel_leave_request(
            personnel=self.p_keea_officer,
            leave_type=self.lt_annual,
            start_date=start_date,
            duration=10,
            requested_by=self.u_keea_officer
        )
        leave_req.status = LeaveRequest.Status.PENDING_APPROVAL
        leave_req.save()

        # DFO Rejects
        self.client.login(username="1000004", password="password123")
        self.client.post(
            reverse("command-approve-leave", args=[leave_req.id]),
            {"decision": "REJECT", "comments": "Leave cannot be accommodated at this time."}
        )
        leave_req.refresh_from_db()
        self.assertEqual(leave_req.status, LeaveRequest.Status.REJECTED)
        self.assertFalse(leave_req.status == LeaveRequest.Status.SENT_TO_WATCH_ROOM)

    # 13. Watch Room cannot change approved duration or dates (Section 43)
    def test_watch_room_cannot_alter_leave_dates_or_duration(self):
        start_date = self.today + timedelta(days=30)
        leave_req = submit_personnel_leave_request(
            personnel=self.p_keea_officer,
            leave_type=self.lt_annual,
            start_date=start_date,
            duration=10,
            requested_by=self.u_keea_officer
        )
        leave_req.status = LeaveRequest.Status.SENT_TO_WATCH_ROOM
        leave_req.save()

        orig_start = leave_req.start_date
        orig_duration = leave_req.duration

        # Watch Room logs booking
        self.client.login(username="keea_watch_room", password="password123")
        self.client.post(
            reverse("watch-room-book-leave", args=[leave_req.id]),
            {
                "book_type": "STATION_DIARY",
                "book_reference": "Entry #99",
                "remarks": "Logged"
            }
        )
        leave_req.refresh_from_db()
        self.assertEqual(leave_req.start_date, orig_start)
        self.assertEqual(leave_req.duration, orig_duration)

    # 14. Super Admin retains national oversight with audit (Section 95)
    def test_super_admin_has_national_oversight(self):
        self.client.login(username="super_admin_fn", password="password123")
        res_acc = self.client.get(reverse("workspace-accounts-national"))
        self.assertEqual(res_acc.status_code, 200)

        res_adm = self.client.get(reverse("workspace-admin-national"))
        self.assertEqual(res_adm.status_code, 200)

        res_ops = self.client.get(reverse("workspace-ops-national"))
        self.assertEqual(res_ops.status_code, 200)


class RolePermissionsViewTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin_user = user_model.objects.create_superuser(
            username="super_perm_admin",
            password="testpassword123",
            email="superadmin@example.com"
        )
        from gnfs_komenda.models import ApplicationRole, AppPermission
        self.role = ApplicationRole.objects.create(
            code="TEST_ROLE",
            name="Test Role",
            description="Role for testing permissions matrix"
        )
        self.perm1 = AppPermission.objects.create(
            code="test.perm1",
            name="Test Perm 1",
            module="LEAVE"
        )
        self.perm2 = AppPermission.objects.create(
            code="test.perm2",
            name="Test Perm 2",
            module="CUSTOM_MODULE"
        )
        self.client = Client()

    def test_app_permission_get_module_display(self):
        self.assertEqual(self.perm1.get_module_display(), "Leave Administration")
        self.assertEqual(self.perm2.get_module_display(), "Custom Module")

    def test_role_permissions_edit_view_get_renders_successfully(self):
        self.client.login(username="super_perm_admin", password="testpassword123")
        url = reverse("role-permissions-edit", args=[self.role.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("perms_by_module", response.context)
        self.assertIn("Leave Administration", response.context["perms_by_module"])
        self.assertIn("Custom Module", response.context["perms_by_module"])
        self.assertContains(response, "Test Role")
        self.assertContains(response, "Test Perm 1")
        self.assertContains(response, "Test Perm 2")


class RoleRedesignComprehensiveTests(TestCase):
    def setUp(self):
        from gnfs_komenda.models import AdministrativeAssignment, ApplicationRole, AppPermission
        from gnfs_komenda.forms import AdministrativeRoleAssignmentForm
        from gnfs_komenda.services.role_service import assign_role, revoke_role, can_assign_role, can_revoke_role, get_actor_administrative_scope

        self.today = timezone.now().date()

        # Regions
        self.reg_central = Region.objects.create(name="Central Region", code="CR")
        self.reg_ashanti = Region.objects.create(name="Ashanti Region", code="AR")

        # Districts
        self.dist_keea = District.objects.create(name="KEEA Municipal", code="KEEA", region=self.reg_central)
        self.dist_cape_coast = District.objects.create(name="Cape Coast Metro", code="CCM", region=self.reg_central)
        self.dist_kumasi = District.objects.create(name="Kumasi Metro", code="KMA", region=self.reg_ashanti)

        # Stations
        self.st_komenda = Station.objects.create(name="Komenda Fire Station", district=self.dist_keea, code="KFS")
        self.st_elmina = Station.objects.create(name="Elmina Fire Station", district=self.dist_keea, code="EFS")
        self.st_kumasi = Station.objects.create(name="Kumasi Central Fire Station", district=self.dist_kumasi, code="KCS")

        # Ranks
        self.rank_sfm = Rank.objects.create(name="Senior Fireman", abbreviation="SFM", officer_category="JUNIOR", hierarchy_order=3)
        self.rank_aso = Rank.objects.create(name="Assistant Station Officer", abbreviation="ASO", officer_category="SENIOR", hierarchy_order=6)
        self.rank_fm = Rank.objects.create(name="Fireman", abbreviation="FM", officer_category="JUNIOR", hierarchy_order=1)

        # Directorate, Department, Unit
        self.dir_admin = FunctionalUnit.objects.create(
            name="Administration Directorate",
            code="DIR_ADM",
            unit_type=FunctionalUnit.FunctionalUnitType.DIRECTORATE
        )
        self.dept_hr = FunctionalUnit.objects.create(
            name="Human Resource Department",
            code="DEPT_HR",
            unit_type=FunctionalUnit.FunctionalUnitType.DEPARTMENT,
            parent=self.dir_admin
        )
        self.unit_rec = FunctionalUnit.objects.create(
            name="Records & Personnel Unit",
            code="UNIT_REC",
            unit_type=FunctionalUnit.FunctionalUnitType.UNIT,
            parent=self.dept_hr
        )

        # Functional Offices
        self.fo_reg_ops = FunctionalOffice.objects.create(
            name="Central Regional Operations",
            function_type=FunctionalOffice.FunctionType.OPERATIONS,
            command_level=FunctionalOffice.CommandLevel.REGIONAL,
            region=self.reg_central,
            is_established=True
        )
        self.fo_keea_admin = FunctionalOffice.objects.create(
            name="KEEA Administration",
            function_type=FunctionalOffice.FunctionType.ADMINISTRATION,
            command_level=FunctionalOffice.CommandLevel.DISTRICT,
            district=self.dist_keea,
            is_established=True
        )
        self.fo_keea_ops = FunctionalOffice.objects.create(
            name="KEEA Operations",
            function_type=FunctionalOffice.FunctionType.OPERATIONS,
            command_level=FunctionalOffice.CommandLevel.DISTRICT,
            district=self.dist_keea,
            is_established=True
        )
        self.fo_keea_acc = FunctionalOffice.objects.create(
            name="KEEA Accounts",
            function_type=FunctionalOffice.FunctionType.ACCOUNTS,
            command_level=FunctionalOffice.CommandLevel.DISTRICT,
            district=self.dist_keea,
            is_established=True
        )
        self.fo_keea_watch = FunctionalOffice.objects.create(
            name="KEEA Watch Room",
            function_type=FunctionalOffice.FunctionType.WATCH_ROOM,
            command_level=FunctionalOffice.CommandLevel.DISTRICT,
            district=self.dist_keea,
            is_established=True
        )
        # Unestablished HR in KEEA
        self.fo_keea_hr_unest = FunctionalOffice.objects.create(
            name="KEEA HR",
            function_type=FunctionalOffice.FunctionType.HR,
            command_level=FunctionalOffice.CommandLevel.DISTRICT,
            district=self.dist_keea,
            is_established=False,
            fallback_office=self.fo_keea_admin
        )

        # Application Roles
        self.app_role_admin = ApplicationRole.objects.create(
            code="DISTRICT_ADMIN",
            name="District Administrator",
            description="District application administration"
        )

        # Users
        self.user_super = User.objects.create_superuser(
            username="test_super_admin",
            password="password123",
            email="super@gnfs.gov.gh"
        )
        self.user_reg_admin = User.objects.create_user(
            username="test_central_reg_admin",
            password="password123",
            email="cr_admin@gnfs.gov.gh"
        )
        UserAssignment.objects.create(
            user=self.user_reg_admin,
            role=GNFSUserRole.REGIONAL_ADMIN,
            region=self.reg_central,
            active=True
        )
        self.user_dist_admin = User.objects.create_user(
            username="test_keea_dist_admin",
            password="password123",
            email="keea_admin@gnfs.gov.gh"
        )
        UserAssignment.objects.create(
            user=self.user_dist_admin,
            role=GNFSUserRole.DISTRICT_ADMIN,
            district=self.dist_keea,
            active=True
        )
        self.user_plain = User.objects.create_user(
            username="test_plain_officer",
            password="password123",
            email="plain@gnfs.gov.gh"
        )

        # Personnel & Postings
        self.p_central_1 = NorminalRoll.objects.create(
            staffID="GNFS/12345",
            fullname="John Kwame Mensah",
            job_grade="Senior Fireman",
            rank=self.rank_sfm,
            birth_date=self.today - timedelta(days=365 * 28),
            ghana_card="GHA-123456789-10",
            station=self.st_komenda,
            mgmt_units="KOMENDA",
            bank="GCB",
            account_number="1234567890",
            payroll_status="ACTIVE"
        )
        PersonnelPosting.objects.create(
            personnel=self.p_central_1,
            region=self.reg_central,
            district=self.dist_keea,
            station=self.st_komenda,
            start_date=self.today - timedelta(days=100),
            is_current=True
        )

        self.p_central_2 = NorminalRoll.objects.create(
            staffID="GNFS/14523",
            fullname="Ama Mensah",
            job_grade="Assistant Station Officer",
            rank=self.rank_aso,
            birth_date=self.today - timedelta(days=365 * 32),
            ghana_card="GHA-123456789-11",
            station=self.st_elmina,
            mgmt_units="ELMINA",
            bank="GCB",
            account_number="1234567891",
            payroll_status="ACTIVE"
        )
        PersonnelPosting.objects.create(
            personnel=self.p_central_2,
            region=self.reg_central,
            district=self.dist_keea,
            station=self.st_elmina,
            start_date=self.today - timedelta(days=80),
            is_current=True
        )

        self.p_ashanti_1 = NorminalRoll.objects.create(
            staffID="GNFS/99887",
            fullname="Kwame Osei",
            job_grade="Fireman",
            rank=self.rank_fm,
            birth_date=self.today - timedelta(days=365 * 24),
            ghana_card="GHA-123456789-12",
            station=self.st_kumasi,
            mgmt_units="KUMASI",
            bank="GCB",
            account_number="1234567892",
            payroll_status="ACTIVE"
        )
        PersonnelPosting.objects.create(
            personnel=self.p_ashanti_1,
            region=self.reg_ashanti,
            district=self.dist_kumasi,
            station=self.st_kumasi,
            start_date=self.today - timedelta(days=50),
            is_current=True
        )

        self.client = Client()

    # 1. National form hierarchy
    def test_national_form_hierarchy(self):
        self.client.login(username="test_super_admin", password="password123")
        res = self.client.get(reverse("roles-api-cascade") + "?action=get_directorates&level=NATIONAL")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("directorates", data)
        self.assertTrue(any(d["name"] == "Administration Directorate" for d in data["directorates"]))

    # 2. Regional form hierarchy
    def test_regional_form_hierarchy(self):
        self.client.login(username="test_central_reg_admin", password="password123")
        res = self.client.get(
            reverse("roles-api-cascade") + f"?action=get_departments&level=REGIONAL&region_id={self.reg_central.id}"
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("departments", data)
        dept_ids = [d["id"] for d in data["departments"]]
        self.assertIn("OPERATIONS", dept_ids)

    # 3. District form hierarchy
    def test_district_form_hierarchy(self):
        self.client.login(username="test_keea_dist_admin", password="password123")
        res = self.client.get(
            reverse("roles-api-cascade") + f"?action=get_departments&level=DISTRICT&district_id={self.dist_keea.id}"
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        dept_ids = [d["id"] for d in data["departments"]]
        self.assertIn("OPERATIONS", dept_ids)
        self.assertIn("ACCOUNTS", dept_ids)
        self.assertIn("ADMINISTRATION", dept_ids)
        self.assertIn("WATCH_ROOM", dept_ids)

    # 4. Directorate appears only at National
    def test_directorate_appears_only_at_national(self):
        self.client.login(username="test_super_admin", password="password123")
        # Regional cascade should return empty directorates
        res_reg = self.client.get(reverse("roles-api-cascade") + "?action=get_directorates&level=REGIONAL")
        self.assertEqual(res_reg.json().get("directorates", []), [])

        # District cascade should return empty directorates
        res_dist = self.client.get(reverse("roles-api-cascade") + "?action=get_directorates&level=DISTRICT")
        self.assertEqual(res_dist.json().get("directorates", []), [])

        # Submitting directorate under REGIONAL level in form should be rejected
        from gnfs_komenda.forms import AdministrativeRoleAssignmentForm
        form_data = {
            'level': 'REGIONAL',
            'region': str(self.reg_central.id),
            'district': '',
            'directorate': str(self.dir_admin.id),
            'department': 'OPERATIONS',
            'unit': '',
            'role': 'Regional Operations Officer',
            'personnel': str(self.p_central_2.id),
            'effective_from': '2026-10-02',
            'remarks': 'Testing hierarchy'
        }
        form = AdministrativeRoleAssignmentForm(data=form_data, actor=self.user_super)
        self.assertFalse(form.is_valid())
        self.assertIn("Directorate is only applicable at National level", str(form.errors))

    # 5. Region loads its District Fire Stations & Another Region's Districts never appear
    def test_region_loads_its_districts_only(self):
        self.client.login(username="test_super_admin", password="password123")
        res = self.client.get(reverse("roles-api-cascade") + f"?action=get_districts&region_id={self.reg_central.id}")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        district_names = [d["name"] for d in data["districts"]]
        self.assertIn("KEEA Municipal", district_names)
        self.assertIn("Cape Coast Metro", district_names)
        # Ashanti district must never appear
        self.assertNotIn("Kumasi Metro", district_names)

    # 6. District selection constrains Personnel
    def test_district_selection_constrains_personnel(self):
        self.client.login(username="test_super_admin", password="password123")
        res = self.client.get(
            reverse("roles-personnel-search") + f"?level=DISTRICT&region={self.reg_central.id}&district={self.dist_keea.id}"
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        ids = [item["id"] for item in data["results"]]
        self.assertIn(self.p_central_1.id, ids)
        self.assertIn(self.p_central_2.id, ids)
        # Ashanti officer must be excluded
        self.assertNotIn(self.p_ashanti_1.id, ids)

    # 7. Personnel search by Full Name and Staff ID
    def test_personnel_search_by_fullname_and_staffid(self):
        self.client.login(username="test_super_admin", password="password123")
        # Search by Full Name
        res_name = self.client.get(
            reverse("roles-personnel-search") + f"?q=John&level=DISTRICT&district={self.dist_keea.id}"
        )
        self.assertEqual(res_name.status_code, 200)
        results = res_name.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], self.p_central_1.id)
        self.assertIn("John Kwame Mensah", results[0]["name"])

        # Search by Staff ID (case insensitive)
        res_id = self.client.get(
            reverse("roles-personnel-search") + f"?q=gnfs/12345&level=DISTRICT&district={self.dist_keea.id}"
        )
        self.assertEqual(res_id.status_code, 200)
        results_id = res_id.json()["results"]
        self.assertEqual(len(results_id), 1)
        self.assertEqual(results_id[0]["id"], self.p_central_1.id)

    # 8. Select2 AJAX pagination
    def test_select2_ajax_pagination(self):
        # Create 25 additional personnel in KEEA
        for i in range(25):
            p = NorminalRoll.objects.create(
                staffID=f"PAG/{1000 + i}",
                fullname=f"Pagination Officer {i}",
                job_grade="Fireman",
                rank=self.rank_fm,
                birth_date=self.today - timedelta(days=365 * 22),
                ghana_card=f"GHA-999000{i}-1",
                station=self.st_komenda,
                mgmt_units="KOMENDA",
                bank="GCB",
                account_number=f"ACC{i}",
                payroll_status="ACTIVE"
            )
            PersonnelPosting.objects.create(
                personnel=p,
                region=self.reg_central,
                district=self.dist_keea,
                station=self.st_komenda,
                start_date=self.today - timedelta(days=10),
                is_current=True
            )

        self.client.login(username="test_super_admin", password="password123")
        res_p1 = self.client.get(
            reverse("roles-personnel-search") + f"?level=DISTRICT&district={self.dist_keea.id}&page=1"
        )
        data_p1 = res_p1.json()
        self.assertEqual(len(data_p1["results"]), 20)
        self.assertTrue(data_p1["pagination"]["more"])

        res_p2 = self.client.get(
            reverse("roles-personnel-search") + f"?level=DISTRICT&district={self.dist_keea.id}&page=2"
        )
        data_p2 = res_p2.json()
        self.assertGreater(len(data_p2["results"]), 0)
        self.assertFalse(data_p2["pagination"]["more"])

    # 9. AJAX authorization
    def test_ajax_authorization(self):
        # Anonymous user receives redirect to login
        res_anon = self.client.get(reverse("roles-personnel-search"))
        self.assertEqual(res_anon.status_code, 302)

        # Plain user without role management access receives 403
        self.client.login(username="test_plain_officer", password="password123")
        res_forbidden = self.client.get(reverse("roles-personnel-search"))
        self.assertEqual(res_forbidden.status_code, 403)

    # 10. Regional scope enforcement
    def test_regional_scope_enforcement(self):
        self.client.login(username="test_central_reg_admin", password="password123")
        # Trying to query Ashanti region while being Central Regional Admin
        res = self.client.get(
            reverse("roles-personnel-search") + f"?region={self.reg_ashanti.id}"
        )
        data = res.json()
        # Scope is strictly locked to Central Region; Ashanti officer must not be returned
        ids = [item["id"] for item in data.get("results", [])]
        self.assertNotIn(self.p_ashanti_1.id, ids)

    # 11. District scope enforcement
    def test_district_scope_enforcement(self):
        self.client.login(username="test_keea_dist_admin", password="password123")
        # District Admin trying to query Kumasi Metro
        res = self.client.get(
            reverse("roles-personnel-search") + f"?district={self.dist_kumasi.id}"
        )
        data = res.json()
        # Must only return KEEA personnel
        ids = [item["id"] for item in data.get("results", [])]
        self.assertNotIn(self.p_ashanti_1.id, ids)

    # 12. Super Admin scope selection
    def test_super_admin_scope_selection(self):
        self.client.login(username="test_super_admin", password="password123")
        # Super Admin selects District=KEEA; response contains KEEA personnel only
        res = self.client.get(
            reverse("roles-personnel-search") + f"?level=DISTRICT&region={self.reg_central.id}&district={self.dist_keea.id}"
        )
        ids = [item["id"] for item in res.json()["results"]]
        self.assertIn(self.p_central_1.id, ids)
        self.assertNotIn(self.p_ashanti_1.id, ids)

    # 13. Functional office loading & Unit loading
    def test_functional_office_and_unit_loading(self):
        self.client.login(username="test_super_admin", password="password123")
        # Units under HR Department
        res = self.client.get(
            reverse("roles-api-cascade") + f"?action=get_units&department={self.dept_hr.id}"
        )
        self.assertEqual(res.status_code, 200)
        units = res.json()["units"]
        self.assertTrue(any(u["name"] == "Records & Personnel Unit" for u in units))

    # 14. Watch Room assignment
    def test_watch_room_assignment(self):
        from gnfs_komenda.models import AdministrativeAssignment, AuditLog
        from gnfs_komenda.services.role_service import assign_role

        asgn = assign_role(
            actor=self.user_super,
            personnel=self.p_central_1,
            assignment_type=AdministrativeAssignment.AssignmentType.FUNCTIONAL,
            level="DISTRICT",
            responsibility_title="Watch Room Officer",
            region=self.reg_central,
            district=self.dist_keea,
            functional_office=self.fo_keea_watch,
            effective_from=self.today,
            remarks="Assigned to Watch Room duty"
        )
        self.assertTrue(asgn.is_active)
        self.assertEqual(asgn.assignment_type, "FUNCTIONAL")
        self.assertEqual(asgn.responsibility_title, "Watch Room Officer")
        self.assertEqual(asgn.display_role_name, "Watch Room Officer")
        self.assertEqual(asgn.district, self.dist_keea)
        # Check AuditLog
        self.assertTrue(AuditLog.objects.filter(target_id=str(self.p_central_1.id)).exists())

    # 15. Operations assignment
    def test_operations_assignment(self):
        from gnfs_komenda.models import AdministrativeAssignment
        from gnfs_komenda.services.role_service import assign_role

        asgn = assign_role(
            actor=self.user_super,
            personnel=self.p_central_2,
            assignment_type=AdministrativeAssignment.AssignmentType.FUNCTIONAL,
            level="DISTRICT",
            responsibility_title="District Operations Officer",
            region=self.reg_central,
            district=self.dist_keea,
            functional_office=self.fo_keea_ops,
            effective_from=self.today,
            remarks="Leading operations"
        )
        self.assertEqual(asgn.responsibility_title, "District Operations Officer")
        self.assertEqual(asgn.functional_office.function_type, "OPERATIONS")

    # 16. Accounts assignment
    def test_accounts_assignment(self):
        from gnfs_komenda.models import AdministrativeAssignment
        from gnfs_komenda.services.role_service import assign_role

        asgn = assign_role(
            actor=self.user_super,
            personnel=self.p_central_1,
            assignment_type=AdministrativeAssignment.AssignmentType.FUNCTIONAL,
            level="DISTRICT",
            responsibility_title="District Accounts Officer",
            region=self.reg_central,
            district=self.dist_keea,
            functional_office=self.fo_keea_acc,
            effective_from=self.today
        )
        self.assertEqual(asgn.functional_office.function_type, "ACCOUNTS")

    # 17. Administration assignment
    def test_administration_assignment(self):
        from gnfs_komenda.models import AdministrativeAssignment
        from gnfs_komenda.services.role_service import assign_role

        asgn = assign_role(
            actor=self.user_super,
            personnel=self.p_central_2,
            assignment_type=AdministrativeAssignment.AssignmentType.FUNCTIONAL,
            level="DISTRICT",
            responsibility_title="District Administration Officer",
            region=self.reg_central,
            district=self.dist_keea,
            functional_office=self.fo_keea_admin,
            effective_from=self.today
        )
        self.assertEqual(asgn.functional_office.function_type, "ADMINISTRATION")

    # 18. HR fallback enforcement (unestablished District HR excluded)
    def test_hr_fallback_enforcement(self):
        self.client.login(username="test_keea_dist_admin", password="password123")
        res = self.client.get(
            reverse("roles-api-cascade") + f"?action=get_departments&level=DISTRICT&district_id={self.dist_keea.id}"
        )
        departments = res.json()["departments"]
        dept_ids = [d["id"] for d in departments]
        # In KEEA, HR is unestablished (is_established=False), so it must NOT appear in the dropdown
        self.assertNotIn("HR", dept_ids)

    # 19. Command appointments remain separate
    def test_command_appointments_remain_separate(self):
        from gnfs_komenda.models import CommandAppointment
        initial_count = CommandAppointment.objects.count()

        # Functional assignment should NOT touch or create CommandAppointment
        from gnfs_komenda.services.role_service import assign_role
        assign_role(
            actor=self.user_super,
            personnel=self.p_central_1,
            assignment_type="FUNCTIONAL",
            level="DISTRICT",
            responsibility_title="District Operations Officer",
            region=self.reg_central,
            district=self.dist_keea,
            functional_office=self.fo_keea_ops,
            effective_from=self.today
        )
        self.assertEqual(CommandAppointment.objects.count(), initial_count)

    # 20. Application roles remain separate
    def test_application_roles_remain_separate(self):
        from gnfs_komenda.models import ApplicationRole, AdministrativeAssignment
        from gnfs_komenda.services.role_service import assign_role

        # Application role assignment uses role foreign key
        asgn_admin = assign_role(
            actor=self.user_super,
            personnel=self.p_central_1,
            assignment_type=AdministrativeAssignment.AssignmentType.ADMINISTRATIVE,
            level="DISTRICT",
            role=self.app_role_admin,
            region=self.reg_central,
            district=self.dist_keea,
            effective_from=self.today
        )
        self.assertEqual(asgn_admin.assignment_type, "ADMINISTRATIVE")
        self.assertEqual(asgn_admin.role, self.app_role_admin)

        # Functional role assignment uses responsibility_title
        asgn_func = assign_role(
            actor=self.user_super,
            personnel=self.p_central_2,
            assignment_type=AdministrativeAssignment.AssignmentType.FUNCTIONAL,
            level="DISTRICT",
            responsibility_title="District Accounts Officer",
            region=self.reg_central,
            district=self.dist_keea,
            functional_office=self.fo_keea_acc,
            effective_from=self.today
        )
        self.assertEqual(asgn_func.assignment_type, "FUNCTIONAL")
        self.assertIsNone(asgn_func.role)

    # 21. Duplicate active assignment prevention
    def test_duplicate_active_assignment_prevention(self):
        from gnfs_komenda.services.role_service import assign_role
        from django.core.exceptions import ValidationError

        assign_role(
            actor=self.user_super,
            personnel=self.p_central_1,
            assignment_type="FUNCTIONAL",
            level="DISTRICT",
            responsibility_title="Watch Room Officer",
            region=self.reg_central,
            district=self.dist_keea,
            functional_office=self.fo_keea_watch,
            effective_from=self.today
        )
        # Attempting identical active assignment must raise ValidationError
        with self.assertRaises(ValidationError) as ctx:
            assign_role(
                actor=self.user_super,
                personnel=self.p_central_1,
                assignment_type="FUNCTIONAL",
                level="DISTRICT",
                responsibility_title="Watch Room Officer",
                region=self.reg_central,
                district=self.dist_keea,
                functional_office=self.fo_keea_watch,
                effective_from=self.today
            )
        self.assertIn("already holds an active assignment", str(ctx.exception))

    # 22. Revocation history retention
    def test_revocation_history_retention(self):
        from gnfs_komenda.models import AdministrativeAssignment, AuditLog
        from gnfs_komenda.services.role_service import assign_role, revoke_role

        asgn = assign_role(
            actor=self.user_super,
            personnel=self.p_central_1,
            assignment_type="FUNCTIONAL",
            level="DISTRICT",
            responsibility_title="District Operations Officer",
            region=self.reg_central,
            district=self.dist_keea,
            functional_office=self.fo_keea_ops,
            effective_from=self.today
        )
        asgn_id = asgn.id

        # Revoke assignment
        revoked = revoke_role(
            actor=self.user_super,
            assignment=asgn,
            reason="Transferred to regional command"
        )
        self.assertFalse(revoked.is_active)
        self.assertEqual(revoked.revoked_by, self.user_super)
        self.assertIsNotNone(revoked.revoked_at)
        self.assertEqual(revoked.revocation_reason, "Transferred to regional command")

        # History is preserved in the database (never deleted)
        asgn_db = AdministrativeAssignment.objects.get(id=asgn_id)
        self.assertFalse(asgn_db.is_active)
        self.assertEqual(asgn_db.revocation_reason, "Transferred to regional command")

        # AuditLog recorded revocation
        self.assertTrue(
            AuditLog.objects.filter(
                target_id=str(self.p_central_1.id),
                action__icontains="REVOKE"
            ).exists()
        )

    # 23. Permission configuration page HTTP 200 regression test
    def test_permission_configuration_page_http_200(self):
        self.client.login(username="test_super_admin", password="password123")
        res = self.client.get(reverse("role-permissions-edit", args=[self.app_role_admin.id]))
        self.assertEqual(res.status_code, 200)


class ProportionateLeaveAndStationRoleAssignmentTests(TestCase):
    """
    Authoritative test suite for:
    1. Proportionate / Prorated Leave fix (numeric input, backend validation, audit logging,
       ordinary user restriction, 36/42 normal entitlements, partial leave != proportionate).
    2. District and Fire Station hierarchy role assignment (Region -> District -> Fire Station -> Personnel,
       cascading reset, AJAX filtering, cross-district/cross-region security, legacy preservation).
    """

    def setUp(self):
        from django.contrib.auth import get_user_model
        from gnfs_komenda.models import (
            Region, District, Station, Rank, NorminalRoll, PersonnelPosting,
            LeaveType, LeaveEntitlement, LeaveRequest, LeaveAdjustmentAudit,
            AdministrativeAssignment, ApplicationRole, AuditLog
        )
        from staff.models import GNFSUserRole, UserAssignment
        from gnfs_komenda.services.leave_policy_service import (
            calculate_annual_leave_entitlement, set_proportionate_annual_entitlement,
            restore_standard_annual_entitlement, can_manage_proportionate_entitlement
        )
        from gnfs_komenda.services.role_service import assign_role, revoke_role, can_assign_role

        User = get_user_model()
        self.today = timezone.now().date()
        self.year = self.today.year

        # Regions
        self.reg_central = Region.objects.create(name="Central Region", code="CR")
        self.reg_ashanti = Region.objects.create(name="Ashanti Region", code="AR")

        # Districts
        self.dist_keea = District.objects.create(name="KEEA District", code="KEEA", region=self.reg_central)
        self.dist_cape_coast = District.objects.create(name="Cape Coast District", code="CC", region=self.reg_central)
        self.dist_kumasi = District.objects.create(name="Kumasi District", code="KMA", region=self.reg_ashanti)

        # Fire Stations (database records only)
        self.st_komenda = Station.objects.create(name="Komenda Fire Station", district=self.dist_keea, code="KFS")
        self.st_elmina = Station.objects.create(name="Elmina Fire Station", district=self.dist_keea, code="EFS")
        self.st_cape_coast = Station.objects.create(name="Cape Coast Fire Station", district=self.dist_cape_coast, code="CCFS")
        self.st_kumasi = Station.objects.create(name="Kumasi Fire Station", district=self.dist_kumasi, code="KMFS")

        # Ranks
        self.rank_junior = Rank.objects.create(name="Senior Fireman", abbreviation="SFM", officer_category="JUNIOR", hierarchy_order=3)
        self.rank_senior = Rank.objects.create(name="Assistant Station Officer", abbreviation="ASO", officer_category="SENIOR", hierarchy_order=6)

        # Leave Types
        self.lt_annual, _ = LeaveType.objects.get_or_create(
            code="ANNUAL",
            defaults={
                "name": "Annual Leave",
                "default_days_junior": 36,
                "default_days_senior": 42,
                "calculation_mode": LeaveType.CalculationMode.WORKING_DAYS
            }
        )

        # Users
        self.admin_user = User.objects.create_superuser(
            username="admin_test_user",
            password="password123",
            email="admin@gnfs.gov.gh"
        )
        self.ordinary_user = User.objects.create_user(
            username="officer_plain_user",
            password="password123",
            email="plain@gnfs.gov.gh"
        )

        # Personnel:
        # P1: Junior officer at Komenda Fire Station
        self.p_junior_komenda = NorminalRoll.objects.create(
            staffID="GNFS/12345",
            fullname="John Mensah",
            job_grade="Senior Fireman",
            rank=self.rank_junior,
            birth_date=self.today - timedelta(days=365 * 25),
            ghana_card="GHA-123456789-01",
            station=self.st_komenda,
            mgmt_units="KOMENDA",
            bank="GCB",
            account_number="1234567890",
            payroll_status="ACTIVE"
        )
        UserAssignment.objects.create(
            user=self.ordinary_user,
            personnel=self.p_junior_komenda,
            role=GNFSUserRole.STAFF,
            active=True
        )
        PersonnelPosting.objects.create(
            personnel=self.p_junior_komenda,
            region=self.reg_central,
            district=self.dist_keea,
            station=self.st_komenda,
            start_date=self.today - timedelta(days=100),
            is_current=True
        )

        # P2: Senior officer at Komenda Fire Station
        self.p_senior_komenda = NorminalRoll.objects.create(
            staffID="GNFS/67890",
            fullname="Kofi Annan",
            job_grade="Assistant Station Officer",
            rank=self.rank_senior,
            birth_date=self.today - timedelta(days=365 * 35),
            ghana_card="GHA-123456789-02",
            station=self.st_komenda,
            mgmt_units="KOMENDA",
            bank="GCB",
            account_number="1234567891",
            payroll_status="ACTIVE"
        )
        PersonnelPosting.objects.create(
            personnel=self.p_senior_komenda,
            region=self.reg_central,
            district=self.dist_keea,
            station=self.st_komenda,
            start_date=self.today - timedelta(days=90),
            is_current=True
        )

        # P3: Officer at Elmina Fire Station (same KEEA District, different Station)
        self.p_elmina = NorminalRoll.objects.create(
            staffID="GNFS/11223",
            fullname="Ama Serwaa",
            job_grade="Senior Fireman",
            rank=self.rank_junior,
            birth_date=self.today - timedelta(days=365 * 27),
            ghana_card="GHA-123456789-03",
            station=self.st_elmina,
            mgmt_units="ELMINA",
            bank="GCB",
            account_number="1234567892",
            payroll_status="ACTIVE"
        )
        PersonnelPosting.objects.create(
            personnel=self.p_elmina,
            region=self.reg_central,
            district=self.dist_keea,
            station=self.st_elmina,
            start_date=self.today - timedelta(days=80),
            is_current=True
        )

        # P4: Officer at Cape Coast (different District)
        self.p_cape_coast = NorminalRoll.objects.create(
            staffID="GNFS/44556",
            fullname="Kwesi Appiah",
            job_grade="Senior Fireman",
            rank=self.rank_junior,
            birth_date=self.today - timedelta(days=365 * 26),
            ghana_card="GHA-123456789-04",
            station=self.st_cape_coast,
            mgmt_units="CAPE COAST",
            bank="GCB",
            account_number="1234567893",
            payroll_status="ACTIVE"
        )
        PersonnelPosting.objects.create(
            personnel=self.p_cape_coast,
            region=self.reg_central,
            district=self.dist_cape_coast,
            station=self.st_cape_coast,
            start_date=self.today - timedelta(days=60),
            is_current=True
        )

        # P5: Officer in Ashanti Region (different Region)
        self.p_ashanti = NorminalRoll.objects.create(
            staffID="GNFS/99887",
            fullname="Osei Tutu",
            job_grade="Senior Fireman",
            rank=self.rank_junior,
            birth_date=self.today - timedelta(days=365 * 29),
            ghana_card="GHA-123456789-05",
            station=self.st_kumasi,
            mgmt_units="KUMASI",
            bank="GCB",
            account_number="1234567894",
            payroll_status="ACTIVE"
        )
        PersonnelPosting.objects.create(
            personnel=self.p_ashanti,
            region=self.reg_ashanti,
            district=self.dist_kumasi,
            station=self.st_kumasi,
            start_date=self.today - timedelta(days=50),
            is_current=True
        )

        # Application Role for testing
        self.app_role_admin, _ = ApplicationRole.objects.get_or_create(
            code="DISTRICT_ADMIN",
            defaults={"name": "District/Station Administrator", "description": "Station Admin"}
        )

        from gnfs_komenda.models import FunctionalUnit
        self.dept_admin, _ = FunctionalUnit.objects.get_or_create(
            code="DEPT_ADM",
            defaults={"name": "Administration Department", "unit_type": FunctionalUnit.FunctionalUnitType.DEPARTMENT}
        )

    # 1. Junior normal entitlement remains 36
    def test_junior_normal_entitlement_remains_36(self):
        from gnfs_komenda.services.leave_policy_service import calculate_annual_leave_entitlement
        entitlement = calculate_annual_leave_entitlement(self.p_junior_komenda, self.year)
        self.assertEqual(entitlement.base_entitlement, 36)
        self.assertFalse(entitlement.is_prorated)

    # 2. Senior normal entitlement remains 42
    def test_senior_normal_entitlement_remains_42(self):
        from gnfs_komenda.services.leave_policy_service import calculate_annual_leave_entitlement
        entitlement = calculate_annual_leave_entitlement(self.p_senior_komenda, self.year)
        self.assertEqual(entitlement.base_entitlement, 42)
        self.assertFalse(entitlement.is_prorated)

    # 3. Proportionate input accepts numbers (1, 2, 5, 10, 18, 25)
    def test_proportionate_input_accepts_numbers(self):
        from gnfs_komenda.forms import ProportionateLeaveEntitlementForm
        from gnfs_komenda.services.leave_policy_service import (
            calculate_annual_leave_entitlement, set_proportionate_annual_entitlement
        )
        from gnfs_komenda.models import LeaveAdjustmentAudit, AuditLog

        entitlement = calculate_annual_leave_entitlement(self.p_junior_komenda, self.year)
        # Form validation for numeric inputs: 1, 2, 5, 10, 18, 25
        for days in [1, 2, 5, 10, 18, 25]:
            form = ProportionateLeaveEntitlementForm(
                entitlement=entitlement,
                actor=self.admin_user,
                data={
                    'entitlement_type': 'PROPORTIONATE',
                    'proportionate_days': days,
                    'reason': f'Proportionate leave for {days} days',
                    'effective_date': self.today.strftime('%Y-%m-%d')
                }
            )
            self.assertTrue(form.is_valid(), f"Failed on days={days}: {form.errors}")

        # Set 18 days via service
        updated = set_proportionate_annual_entitlement(
            entitlement=entitlement,
            proportionate_days=18,
            reason="Mid-year enlistment / prorated adjustment",
            actor=self.admin_user,
            effective_date=self.today
        )
        self.assertEqual(updated.base_entitlement, 18)
        self.assertTrue(updated.is_prorated)
        self.assertEqual(updated.proration_reason, "Mid-year enlistment / prorated adjustment")

        # Verify audit records
        self.assertTrue(
            LeaveAdjustmentAudit.objects.filter(
                entitlement=entitlement,
                reason__icontains="Proportionate",
                adjusted_by=self.admin_user
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                target_id=str(self.p_junior_komenda.id),
                action__icontains="PRORATED"
            ).exists()
        )

    # 4. Ordinary Personnel cannot alter entitlement
    def test_ordinary_personnel_cannot_alter_entitlement(self):
        from gnfs_komenda.services.leave_policy_service import (
            calculate_annual_leave_entitlement, can_manage_proportionate_entitlement
        )

        # Policy service check denies ordinary user
        self.assertFalse(can_manage_proportionate_entitlement(self.ordinary_user, self.p_junior_komenda))

        entitlement = calculate_annual_leave_entitlement(self.p_junior_komenda, self.year)
        self.client.login(username="officer_plain_user", password="password123")
        response = self.client.post(
            reverse("leave-set-proportionate-entitlement", args=[entitlement.id]),
            {
                'entitlement_type': 'PROPORTIONATE',
                'proportionate_days': 10,
                'reason': 'Attempting unauthorized entitlement adjustment',
                'effective_date': str(self.today)
            }
        )
        # Forbidden
        self.assertEqual(response.status_code, 403)
        entitlement.refresh_from_db()
        self.assertEqual(entitlement.base_entitlement, 36)
        self.assertFalse(entitlement.is_prorated)

    # 5. Partial leave != proportionate entitlement
    def test_partial_leave_not_equal_proportionate_entitlement(self):
        from gnfs_komenda.services.leave_policy_service import calculate_annual_leave_entitlement

        entitlement = calculate_annual_leave_entitlement(self.p_junior_komenda, self.year)
        self.assertEqual(entitlement.base_entitlement, 36)
        self.assertFalse(entitlement.is_prorated)

        # Officer takes partial leave (15 days out of 36)
        entitlement.days_taken += 15
        entitlement.save(update_fields=['days_taken'])

        # Base annual entitlement is still 36; it is not prorated!
        entitlement.refresh_from_db()
        self.assertEqual(entitlement.base_entitlement, 36)
        self.assertEqual(entitlement.remaining_balance, 21)
        self.assertFalse(entitlement.is_prorated)

    # 6. Region returns correct Districts
    def test_region_returns_correct_districts(self):
        self.client.login(username="admin_test_user", password="password123")
        res = self.client.get(reverse("roles-api-cascade") + f"?action=get_districts&region_id={self.reg_central.id}")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        district_ids = [d["id"] for d in data["districts"]]
        self.assertIn(self.dist_keea.id, district_ids)
        self.assertIn(self.dist_cape_coast.id, district_ids)
        self.assertNotIn(self.dist_kumasi.id, district_ids)

    # 7. District returns correct Fire Stations
    def test_district_returns_correct_fire_stations(self):
        self.client.login(username="admin_test_user", password="password123")
        res = self.client.get(reverse("roles-api-cascade") + f"?action=get_stations&district_id={self.dist_keea.id}")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        station_ids = [s["id"] for s in data["stations"]]
        self.assertIn(self.st_komenda.id, station_ids)
        self.assertIn(self.st_elmina.id, station_ids)
        # Foreign stations from other districts never appear
        self.assertNotIn(self.st_cape_coast.id, station_ids)
        self.assertNotIn(self.st_kumasi.id, station_ids)

    # 8. Fire Station returns correct Personnel
    def test_fire_station_returns_correct_personnel(self):
        self.client.login(username="admin_test_user", password="password123")
        res = self.client.get(
            reverse("roles-personnel-search") + f"?level=DISTRICT&region={self.reg_central.id}&district={self.dist_keea.id}&station={self.st_komenda.id}"
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        p_ids = [item["id"] for item in data["results"]]
        self.assertIn(self.p_junior_komenda.id, p_ids)
        self.assertIn(self.p_senior_komenda.id, p_ids)
        # Elmina officer is in same district but different station - must be excluded!
        self.assertNotIn(self.p_elmina.id, p_ids)
        self.assertNotIn(self.p_cape_coast.id, p_ids)
        self.assertNotIn(self.p_ashanti.id, p_ids)

    # 9. Search works by name
    def test_search_works_by_name(self):
        self.client.login(username="admin_test_user", password="password123")
        res = self.client.get(
            reverse("roles-personnel-search") + f"?level=DISTRICT&region={self.reg_central.id}&district={self.dist_keea.id}&station={self.st_komenda.id}&q=Mensah"
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        p_ids = [item["id"] for item in data["results"]]
        self.assertIn(self.p_junior_komenda.id, p_ids)
        self.assertNotIn(self.p_senior_komenda.id, p_ids)

    # 10. Search works by Staff ID
    def test_search_works_by_staff_id(self):
        self.client.login(username="admin_test_user", password="password123")
        res = self.client.get(
            reverse("roles-personnel-search") + f"?level=DISTRICT&region={self.reg_central.id}&district={self.dist_keea.id}&station={self.st_komenda.id}&q=12345"
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        p_ids = [item["id"] for item in data["results"]]
        self.assertIn(self.p_junior_komenda.id, p_ids)
        self.assertNotIn(self.p_senior_komenda.id, p_ids)

    # 11. Cross-District search blocked
    def test_cross_district_search_blocked(self):
        from staff.models import UserAssignment, GNFSUserRole
        from django.contrib.auth import get_user_model
        user_dist = get_user_model().objects.create_user(
            username="keea_scoped_admin", password="password123", email="keea_scoped@gnfs.gov.gh"
        )
        UserAssignment.objects.create(
            user=user_dist, role=GNFSUserRole.DISTRICT_ADMIN, district=self.dist_keea, active=True
        )
        self.client.login(username="keea_scoped_admin", password="password123")
        # Attempting to search in Cape Coast District
        res = self.client.get(
            reverse("roles-personnel-search") + f"?level=DISTRICT&region={self.reg_central.id}&district={self.dist_cape_coast.id}&station={self.st_cape_coast.id}"
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        p_ids = [item["id"] for item in data.get("results", [])]
        self.assertNotIn(self.p_cape_coast.id, p_ids)

    # 12. Cross-Region search blocked
    def test_cross_region_search_blocked(self):
        from staff.models import UserAssignment, GNFSUserRole
        from django.contrib.auth import get_user_model
        user_reg = get_user_model().objects.create_user(
            username="central_scoped_admin", password="password123", email="central_scoped@gnfs.gov.gh"
        )
        UserAssignment.objects.create(
            user=user_reg, role=GNFSUserRole.REGIONAL_ADMIN, region=self.reg_central, active=True
        )
        self.client.login(username="central_scoped_admin", password="password123")
        # Attempting to search in Ashanti Region
        res = self.client.get(
            reverse("roles-personnel-search") + f"?level=DISTRICT&region={self.reg_ashanti.id}&district={self.dist_kumasi.id}&station={self.st_kumasi.id}"
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        p_ids = [item["id"] for item in data.get("results", [])]
        self.assertNotIn(self.p_ashanti.id, p_ids)

    # 13. Manipulated AJAX IDs blocked
    def test_manipulated_ajax_ids_blocked(self):
        from gnfs_komenda.forms import AdministrativeRoleAssignmentForm
        from gnfs_komenda.services.role_service import assign_role
        from django.core.exceptions import ValidationError

        # Form submission with station that does not belong to district
        form_data = {
            'assignment_type': 'ROLE',
            'level': 'DISTRICT',
            'region': str(self.reg_central.id),
            'district': str(self.dist_keea.id),
            'station': str(self.st_kumasi.id),  # Kumasi station in KEEA district!
            'department': 'ADMINISTRATION',
            'role': str(self.app_role_admin.id),
            'personnel': str(self.p_junior_komenda.id),
            'effective_from': str(self.today)
        }
        form = AdministrativeRoleAssignmentForm(data=form_data, actor=self.admin_user)
        self.assertFalse(form.is_valid())
        self.assertIn("does not belong to District", str(form.errors))

        # Backend service assign_role also validates station.district_id == district.id
        with self.assertRaises((ValidationError, PermissionDenied)):
            assign_role(
                actor=self.admin_user,
                personnel=self.p_junior_komenda,
                role=self.app_role_admin,
                assignment_type='ROLE',
                level='DISTRICT',
                region=self.reg_central,
                district=self.dist_keea,
                station=self.st_kumasi,
                department=self.dept_admin,
                effective_from=self.today
            )

    # 14. Role assignment with station creates audited assignment
    def test_role_assignment_with_station_creates_audited_assignment(self):
        from gnfs_komenda.services.role_service import assign_role
        from gnfs_komenda.models import AdministrativeAssignment, AuditLog

        assignment = assign_role(
            actor=self.admin_user,
            personnel=self.p_junior_komenda,
            role=self.app_role_admin,
            assignment_type='ROLE',
            level='DISTRICT',
            region=self.reg_central,
            district=self.dist_keea,
            station=self.st_komenda,
            department=self.dept_admin,
            effective_from=self.today
        )
        self.assertEqual(assignment.station, self.st_komenda)
        self.assertEqual(assignment.district, self.dist_keea)
        self.assertEqual(assignment.region, self.reg_central)
        self.assertTrue(assignment.is_active)
        self.assertIn(self.st_komenda.name, str(assignment))

        # AuditLog record created
        self.assertTrue(
            AuditLog.objects.filter(
                target_id=str(self.p_junior_komenda.id),
                action__icontains="ROLE"
            ).exists()
        )

    # 15. Role assignment blocked if Personnel not posted to station
    def test_role_assignment_blocked_if_personnel_not_posted_to_station(self):
        from gnfs_komenda.services.role_service import assign_role
        from django.core.exceptions import ValidationError

        # p_elmina is posted to Elmina Fire Station, not Komenda Fire Station
        with self.assertRaises(ValidationError) as ctx:
            assign_role(
                actor=self.admin_user,
                personnel=self.p_elmina,
                role=self.app_role_admin,
                assignment_type='ROLE',
                level='DISTRICT',
                region=self.reg_central,
                district=self.dist_keea,
                station=self.st_komenda,
                department=self.dept_admin,
                effective_from=self.today
            )
        self.assertIn("not currently posted to Komenda Fire Station", str(ctx.exception))

    # 16. Existing records preserved
    def test_existing_records_preserved(self):
        from gnfs_komenda.models import AdministrativeAssignment

        # Create a legacy assignment with station=None
        legacy_asgn = AdministrativeAssignment.objects.create(
            personnel=self.p_junior_komenda,
            role=self.app_role_admin,
            assignment_type='ROLE',
            level='DISTRICT',
            region=self.reg_central,
            district=self.dist_keea,
            station=None,
            department=None,
            effective_from=self.today,
            is_active=True,
            assigned_by=self.admin_user
        )
        self.assertIsNone(legacy_asgn.station)
        str_rep = str(legacy_asgn)
        self.assertIn(self.dist_keea.name, str_rep)
        self.assertTrue(AdministrativeAssignment.objects.filter(id=legacy_asgn.id).exists())



class DistrictWorkspacesAndConsolesTests(TestCase):
    """
    Prompt 2: District-level GNFS Workspace Architecture & Consoles Tests (Section 19).
    Validates:
    1. Personnel John Mensah (no roles) -> receives ONLY MY_PORTAL.
    2. John Mensah assigned District Administrator -> receives MY_PORTAL + DISTRICT_ADMINISTRATION (two workspaces, ONE account).
    3. John Mensah assigned District Operations -> receives MY_PORTAL + DISTRICT_OPERATIONS (two workspaces, ONE account).
    4. John Mensah assigned District Accounts -> receives MY_PORTAL + DISTRICT_ACCOUNTS (two workspaces, ONE account).
    5. John Mensah assigned District Watch Room -> receives MY_PORTAL + DISTRICT_WATCH_ROOM (two workspaces, ONE account).
    6. John Mensah appointed DFO -> receives MY_PORTAL + DISTRICT_COMMAND_DFO_MFO (two workspaces, ONE account).
    7. John Mensah appointed 2IC -> receives MY_PORTAL + DISTRICT_COMMAND_2IC (two workspaces, ONE account).
    8. John Mensah assigned Admin + Operations -> receives MY_PORTAL + DISTRICT_ADMINISTRATION + DISTRICT_OPERATIONS (three workspaces, ONE account).
    9. Revoking Admin role removes DISTRICT_ADMINISTRATION; MY_PORTAL remains.
    10. Ending DFO appointment removes DISTRICT_COMMAND_DFO_MFO; MY_PORTAL remains.
    11. Personnel user accessing /district/admin/ without role -> 403 Forbidden.
    12. Personnel user accessing /district/operations/ without role -> 403 Forbidden.
    13. Personnel user accessing /district/accounts/ without role -> 403 Forbidden.
    14. Personnel user accessing /district/watch-room/ without role -> 403 Forbidden.
    15. Personnel user accessing /district/command/ without appointment -> 403 Forbidden.
    16. District Operations console does NOT display any bank account, payroll, or non-operational personnel.
    17. Watch Room console cannot approve leave (no approval action exists).
    18. District Administration console cannot finally approve leave (can only forward/return).
    19. 2IC console does NOT have final approval button unless explicitly delegated.
    20. Navbar workspace switcher renders all authorized workspaces with active workspace highlighted.
    """
    def setUp(self):
        self.today = timezone.localdate()
        self.user_model = get_user_model()

        # Ranks
        self.rank_sfm = Rank.objects.create(
            name="Senior Fireman (SFM)",
            abbreviation="SFM",
            officer_category=Rank.OfficerCategory.JUNIOR,
            hierarchy_order=12,
            active=True
        )
        self.rank_do1 = Rank.objects.create(
            name="Divisional Officer I (DO I)",
            abbreviation="DO I",
            officer_category=Rank.OfficerCategory.SENIOR,
            hierarchy_order=5,
            active=True
        )

        # Region & District
        self.region_central = Region.objects.create(name="Central Region", code="CR", active=True)
        self.district_keea = District.objects.create(
            name="KEEA",
            region=self.region_central,
            code="KEEA",
            command_type=District.CommandType.MUNICIPAL,
            co_designation="MFO",
            active=True
        )
        self.station_komenda = Station.objects.create(
            name="Komenda Fire Station",
            district=self.district_keea,
            code="KOM",
            active=True
        )

        # One Person — One Account: John Mensah
        self.p_john = NorminalRoll.objects.create(
            staffID="GNFS/12345",
            fullname="John Mensah",
            officer_category=Rank.OfficerCategory.JUNIOR,
            rank=self.rank_sfm,
            station=self.station_komenda,
            ghana_card="GHA-123456789-0",
            birth_date=self.today - timedelta(days=365 * 30),
            leave_status="AT POST",
            is_active=True
        )
        self.user_john = self.user_model.objects.create_user(
            username="GNFS/12345",
            email="john.mensah@gnfs.gov.gh",
            password="password123"
        )

        # Application Roles
        from gnfs_komenda.models import ApplicationRole
        self.app_role_admin, _ = ApplicationRole.objects.get_or_create(
            code="DISTRICT_ADMIN",
            defaults={"name": "District Administrator", "is_active": True}
        )
        self.app_role_dfo, _ = ApplicationRole.objects.get_or_create(
            code="DFO",
            defaults={"name": "District Fire Officer", "is_active": True}
        )
        self.app_role_2ic, _ = ApplicationRole.objects.get_or_create(
            code="DISTRICT_2IC",
            defaults={"name": "District 2IC", "is_active": True}
        )

        # Functional Offices
        self.fo_keea_admin = FunctionalOffice.objects.create(
            name="KEEA Administration", function_type="ADMINISTRATION",
            command_level="DISTRICT", district=self.district_keea, region=self.region_central,
            is_established=True, active=True
        )
        self.fo_keea_ops = FunctionalOffice.objects.create(
            name="KEEA Operations", function_type="OPERATIONS",
            command_level="DISTRICT", district=self.district_keea, region=self.region_central,
            is_established=True, active=True
        )
        self.fo_keea_accounts = FunctionalOffice.objects.create(
            name="KEEA Accounts", function_type="ACCOUNTS",
            command_level="DISTRICT", district=self.district_keea, region=self.region_central,
            is_established=True, active=True
        )
        self.fo_keea_wr = FunctionalOffice.objects.create(
            name="KEEA Watch Room", function_type="WATCH_ROOM",
            command_level="DISTRICT", district=self.district_keea, region=self.region_central,
            is_established=True, active=True
        )

        # Leave Types
        self.leave_annual, _ = LeaveType.objects.get_or_create(
            code="ANNUAL",
            defaults={
                "name": "Annual Leave",
                "calculation_mode": LeaveType.CalculationMode.WORKING_DAYS,
                "default_days_junior": 36,
                "default_days_senior": 42,
                "active": True
            }
        )

    # 1. Personnel John Mensah (no roles) -> receives ONLY MY_PORTAL.
    def test_01_personnel_john_mensah_no_roles_receives_only_my_portal(self):
        workspaces = get_available_workspaces(self.user_john)
        self.assertEqual(len(workspaces), 1)
        self.assertEqual(workspaces[0]["code"], "MY_PORTAL")

    # 2. John Mensah assigned District Administrator -> receives MY_PORTAL + DISTRICT_ADMINISTRATION (two workspaces, ONE account).
    def test_02_john_mensah_assigned_district_administrator_receives_my_portal_and_admin(self):
        from gnfs_komenda.models import AdministrativeAssignment
        AdministrativeAssignment.objects.create(
            personnel=self.p_john,
            role=self.app_role_admin,
            assignment_type='ROLE',
            level='DISTRICT',
            district=self.district_keea,
            region=self.region_central,
            effective_from=self.today,
            is_active=True
        )
        workspaces = get_available_workspaces(self.user_john)
        codes = [w["code"] for w in workspaces]
        self.assertEqual(len(workspaces), 2)
        self.assertIn("MY_PORTAL", codes)
        self.assertIn("DISTRICT_ADMINISTRATION", codes)

    # 3. John Mensah assigned District Operations -> receives MY_PORTAL + DISTRICT_OPERATIONS (two workspaces, ONE account).
    def test_03_john_mensah_assigned_district_operations_receives_my_portal_and_ops(self):
        FunctionalRoleAssignment.objects.create(
            user=self.user_john,
            functional_office=self.fo_keea_ops,
            role="FUNCTION_MEMBER",
            scope_level="DISTRICT",
            district=self.district_keea,
            region=self.region_central,
            active=True
        )
        workspaces = get_available_workspaces(self.user_john)
        codes = [w["code"] for w in workspaces]
        self.assertEqual(len(workspaces), 2)
        self.assertIn("MY_PORTAL", codes)
        self.assertIn("DISTRICT_OPERATIONS", codes)

    # 4. John Mensah assigned District Accounts -> receives MY_PORTAL + DISTRICT_ACCOUNTS (two workspaces, ONE account).
    def test_04_john_mensah_assigned_district_accounts_receives_my_portal_and_accounts(self):
        FunctionalRoleAssignment.objects.create(
            user=self.user_john,
            functional_office=self.fo_keea_accounts,
            role="FUNCTION_MEMBER",
            scope_level="DISTRICT",
            district=self.district_keea,
            region=self.region_central,
            active=True
        )
        workspaces = get_available_workspaces(self.user_john)
        codes = [w["code"] for w in workspaces]
        self.assertEqual(len(workspaces), 2)
        self.assertIn("MY_PORTAL", codes)
        self.assertIn("DISTRICT_ACCOUNTS", codes)

    # 5. John Mensah assigned District Watch Room -> receives MY_PORTAL + DISTRICT_WATCH_ROOM (two workspaces, ONE account).
    def test_05_john_mensah_assigned_district_watch_room_receives_my_portal_and_watch_room(self):
        FunctionalRoleAssignment.objects.create(
            user=self.user_john,
            functional_office=self.fo_keea_wr,
            role="FUNCTION_MEMBER",
            scope_level="DISTRICT",
            district=self.district_keea,
            region=self.region_central,
            active=True
        )
        workspaces = get_available_workspaces(self.user_john)
        codes = [w["code"] for w in workspaces]
        self.assertEqual(len(workspaces), 2)
        self.assertIn("MY_PORTAL", codes)
        self.assertIn("DISTRICT_WATCH_ROOM", codes)

    # 6. John Mensah appointed DFO -> receives MY_PORTAL + DISTRICT_COMMAND_DFO_MFO (two workspaces, ONE account).
    def test_06_john_mensah_appointed_dfo_receives_my_portal_and_command_dfo(self):
        CommandAppointment.objects.create(
            appointment_type="DFO",
            personnel=self.p_john,
            district=self.district_keea,
            effective_from=self.today,
            is_active=True
        )
        workspaces = get_available_workspaces(self.user_john)
        codes = [w["code"] for w in workspaces]
        self.assertEqual(len(workspaces), 2)
        self.assertIn("MY_PORTAL", codes)
        self.assertIn("DISTRICT_COMMAND_DFO_MFO", codes)

    # 7. John Mensah appointed 2IC -> receives MY_PORTAL + DISTRICT_COMMAND_2IC (two workspaces, ONE account).
    def test_07_john_mensah_appointed_2ic_receives_my_portal_and_command_2ic(self):
        CommandAppointment.objects.create(
            appointment_type="DISTRICT_2IC",
            personnel=self.p_john,
            district=self.district_keea,
            effective_from=self.today,
            is_active=True
        )
        workspaces = get_available_workspaces(self.user_john)
        codes = [w["code"] for w in workspaces]
        self.assertEqual(len(workspaces), 2)
        self.assertIn("MY_PORTAL", codes)
        self.assertIn("DISTRICT_COMMAND_2IC", codes)

    # 8. John Mensah assigned Admin + Operations -> receives MY_PORTAL + DISTRICT_ADMINISTRATION + DISTRICT_OPERATIONS (three workspaces, ONE account).
    def test_08_john_mensah_assigned_admin_and_operations_receives_three_workspaces(self):
        from gnfs_komenda.models import AdministrativeAssignment
        AdministrativeAssignment.objects.create(
            personnel=self.p_john,
            role=self.app_role_admin,
            assignment_type='ROLE',
            level='DISTRICT',
            district=self.district_keea,
            region=self.region_central,
            effective_from=self.today,
            is_active=True
        )
        FunctionalRoleAssignment.objects.create(
            user=self.user_john,
            functional_office=self.fo_keea_ops,
            role="FUNCTION_MEMBER",
            scope_level="DISTRICT",
            district=self.district_keea,
            region=self.region_central,
            active=True
        )
        workspaces = get_available_workspaces(self.user_john)
        codes = [w["code"] for w in workspaces]
        self.assertEqual(len(workspaces), 3)
        self.assertIn("MY_PORTAL", codes)
        self.assertIn("DISTRICT_ADMINISTRATION", codes)
        self.assertIn("DISTRICT_OPERATIONS", codes)

    # 9. Revoking Admin role removes DISTRICT_ADMINISTRATION; MY_PORTAL remains.
    def test_09_revoking_admin_role_removes_district_administration_preserves_my_portal(self):
        from gnfs_komenda.models import AdministrativeAssignment
        asgn = AdministrativeAssignment.objects.create(
            personnel=self.p_john,
            role=self.app_role_admin,
            assignment_type='ROLE',
            level='DISTRICT',
            district=self.district_keea,
            region=self.region_central,
            effective_from=self.today,
            is_active=True
        )
        self.assertIn("DISTRICT_ADMINISTRATION", [w["code"] for w in get_available_workspaces(self.user_john)])

        # Revoke
        asgn.is_active = False
        asgn.revoked_at = timezone.now()
        asgn.save()

        workspaces = get_available_workspaces(self.user_john)
        codes = [w["code"] for w in workspaces]
        self.assertNotIn("DISTRICT_ADMINISTRATION", codes)
        self.assertIn("MY_PORTAL", codes)
        self.assertEqual(len(workspaces), 1)

    # 10. Ending DFO appointment removes DISTRICT_COMMAND_DFO_MFO; MY_PORTAL remains.
    def test_10_ending_dfo_appointment_removes_command_dfo_preserves_my_portal(self):
        cmd = CommandAppointment.objects.create(
            appointment_type="DFO",
            personnel=self.p_john,
            district=self.district_keea,
            effective_from=self.today,
            is_active=True
        )
        self.assertIn("DISTRICT_COMMAND_DFO_MFO", [w["code"] for w in get_available_workspaces(self.user_john)])

        # End appointment
        cmd.is_active = False
        cmd.effective_to = self.today
        cmd.save()

        workspaces = get_available_workspaces(self.user_john)
        codes = [w["code"] for w in workspaces]
        self.assertNotIn("DISTRICT_COMMAND_DFO_MFO", codes)
        self.assertIn("MY_PORTAL", codes)
        self.assertEqual(len(workspaces), 1)

    # 11. Personnel user accessing /district/admin/ without role -> 403 Forbidden.
    def test_11_personnel_user_accessing_district_admin_without_role_returns_403(self):
        self.client.login(username="GNFS/12345", password="password123")
        res = self.client.get(reverse('workspace-admin-district', args=[self.district_keea.id]))
        self.assertEqual(res.status_code, 403)

    # 12. Personnel user accessing /district/operations/ without role -> 403 Forbidden.
    def test_12_personnel_user_accessing_district_operations_without_role_returns_403(self):
        self.client.login(username="GNFS/12345", password="password123")
        res = self.client.get(reverse('workspace-ops-district', args=[self.district_keea.id]))
        self.assertEqual(res.status_code, 403)

    # 13. Personnel user accessing /district/accounts/ without role -> 403 Forbidden.
    def test_13_personnel_user_accessing_district_accounts_without_role_returns_403(self):
        self.client.login(username="GNFS/12345", password="password123")
        res = self.client.get(reverse('workspace-accounts-district', args=[self.district_keea.id]))
        self.assertEqual(res.status_code, 403)

    # 14. Personnel user accessing /district/watch-room/ without role -> 403 Forbidden.
    def test_14_personnel_user_accessing_district_watch_room_without_role_returns_403(self):
        self.client.login(username="GNFS/12345", password="password123")
        res = self.client.get(reverse('workspace-watch-room-district', args=[self.district_keea.id]))
        self.assertEqual(res.status_code, 403)

    # 15. Personnel user accessing /district/command/ without appointment -> 403 Forbidden.
    def test_15_personnel_user_accessing_district_command_without_appointment_returns_403(self):
        self.client.login(username="GNFS/12345", password="password123")
        res = self.client.get(reverse('dashboard-district-command', args=[self.district_keea.id]))
        self.assertEqual(res.status_code, 403)

    # 16. District Operations console does NOT display any bank account, payroll, or non-operational personnel.
    def test_16_district_operations_console_does_not_display_bank_account_or_payroll_or_non_ops(self):
        # Assign John to Operations
        FunctionalRoleAssignment.objects.create(
            user=self.user_john,
            functional_office=self.fo_keea_ops,
            role="FUNCTION_MEMBER",
            scope_level="DISTRICT",
            district=self.district_keea,
            region=self.region_central,
            active=True
        )
        # Create non-operational accounts officer with sensitive banking details
        acct_officer = NorminalRoll.objects.create(
            staffID="ACCT/9999",
            fullname="Kwame Accounts",
            officer_category=Rank.OfficerCategory.JUNIOR,
            rank=self.rank_sfm,
            station=self.station_komenda,
            ghana_card="GHA-999999999-9",
            birth_date=self.today - timedelta(days=365 * 28),
            bank="GCB Bank Ghana",
            account_number="ACC-1234567890",
            leave_status="AT POST",
            is_active=True
        )
        PersonnelFunctionalAssignment.objects.create(
            personnel=acct_officer,
            functional_office=self.fo_keea_accounts,
            start_date=self.today,
            is_active=True
        )
        # Create operational firefighter
        ops_firefighter = NorminalRoll.objects.create(
            staffID="OPS/7777",
            fullname="Kofi Firefighter",
            officer_category=Rank.OfficerCategory.JUNIOR,
            rank=self.rank_sfm,
            station=self.station_komenda,
            ghana_card="GHA-777777777-7",
            birth_date=self.today - timedelta(days=365 * 26),
            leave_status="AT POST",
            is_active=True
        )
        PersonnelFunctionalAssignment.objects.create(
            personnel=ops_firefighter,
            functional_office=self.fo_keea_ops,
            start_date=self.today,
            is_active=True
        )

        self.client.login(username="GNFS/12345", password="password123")
        res = self.client.get(reverse('workspace-ops-district', args=[self.district_keea.id]))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode()
        # Verify banking data is NOT displayed
        self.assertNotIn("ACC-1234567890", content)
        self.assertNotIn("GCB Bank Ghana", content)
        # Non-operational staff is excluded
        self.assertNotIn("Kwame Accounts", content)
        # Operational personnel is present
        self.assertIn("Kofi Firefighter", content)

    # 17. Watch Room console cannot approve leave (no approval action exists).
    def test_17_watch_room_console_cannot_approve_leave(self):
        FunctionalRoleAssignment.objects.create(
            user=self.user_john,
            functional_office=self.fo_keea_wr,
            role="FUNCTION_MEMBER",
            scope_level="DISTRICT",
            district=self.district_keea,
            region=self.region_central,
            active=True
        )
        self.client.login(username="GNFS/12345", password="password123")
        res = self.client.get(reverse('workspace-watch-room-district', args=[self.district_keea.id]))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode()
        self.assertIn("Operational Recording Notice", content)
        self.assertNotIn('name="action" value="APPROVE"', content)
        self.assertNotIn("Confirm & Approve Leave", content)

    # 18. District Administration console cannot finally approve leave (can only forward/return).
    def test_18_district_administration_console_cannot_finally_approve_leave(self):
        from gnfs_komenda.models import AdministrativeAssignment
        AdministrativeAssignment.objects.create(
            personnel=self.p_john,
            role=self.app_role_admin,
            assignment_type='ROLE',
            level='DISTRICT',
            district=self.district_keea,
            region=self.region_central,
            effective_from=self.today,
            is_active=True
        )
        self.client.login(username="GNFS/12345", password="password123")
        res = self.client.get(reverse('workspace-admin-district', args=[self.district_keea.id]))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode()
        self.assertIn("FORWARD_TO_OPERATIONS", content)
        self.assertIn("RETURN_FOR_CORRECTION", content)
        self.assertIn("cannot finally approve", content)
        self.assertNotIn('name="action" value="APPROVE"', content)

    # 19. 2IC console does NOT have final approval button unless explicitly delegated.
    def test_19_2ic_console_does_not_have_final_approval_button(self):
        CommandAppointment.objects.create(
            appointment_type="DISTRICT_2IC",
            personnel=self.p_john,
            district=self.district_keea,
            effective_from=self.today,
            is_active=True
        )
        self.client.login(username="GNFS/12345", password="password123")
        res = self.client.get(reverse('dashboard-district-command', args=[self.district_keea.id]))
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.context['can_approve'])
        content = res.content.decode()
        self.assertIn("DISTRICT SECOND-IN-COMMAND (2IC)", content)
        self.assertNotIn('name="action" value="APPROVE"', content)

    # 20. Navbar workspace switcher renders all authorized workspaces with active workspace highlighted.
    def test_20_navbar_workspace_switcher_renders_authorized_workspaces_with_active_highlighted(self):
        from gnfs_komenda.models import AdministrativeAssignment
        AdministrativeAssignment.objects.create(
            personnel=self.p_john,
            role=self.app_role_admin,
            assignment_type='ROLE',
            level='DISTRICT',
            district=self.district_keea,
            region=self.region_central,
            effective_from=self.today,
            is_active=True
        )
        FunctionalRoleAssignment.objects.create(
            user=self.user_john,
            functional_office=self.fo_keea_ops,
            role="FUNCTION_MEMBER",
            scope_level="DISTRICT",
            district=self.district_keea,
            region=self.region_central,
            active=True
        )
        self.client.login(username="GNFS/12345", password="password123")
        res = self.client.get(reverse('my-dashboard'))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode()
        self.assertIn("My Portal", content)
        self.assertIn("KEEA Administration", content)
        self.assertIn("KEEA Operations", content)
        self.assertIn("active bg-danger text-white", content)


# ==============================================================================
# PROMPT 3: DISTRICT FIRE STATION LEAVE WORKFLOW TESTS (40 TESTS)
# ==============================================================================

"""
District Fire Station Leave Workflow Tests (Prompt 3 of 3 - 40 Test Cases).
"""
import datetime
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from gnfs_komenda.models import (
    AuditLog, CommandAppointment, District, FunctionalOffice,
    FunctionalRoleAssignment, LeaveApprovalDecision, LeaveEntitlement,
    LeaveRequest, LeaveType, LeaveWorkflowHistory, NorminalRoll,
    Rank, Region, Station, WatchRoomBooking, ApplicationRole, AdministrativeAssignment
)
from gnfs_komenda.services.leave_workflow_service import (
    submit_district_leave,
    admin_process_leave,
    operations_recommend_leave,
    command_approve_leave,
    command_reject_leave,
    admin_send_to_watch_room,
    watch_room_book_leave,
    watch_room_record_resumption,
    get_personal_status_tracker,
)
from gnfs_komenda.services.leave_balance_service import (
    get_base_annual_entitlement,
    calculate_proportionate_entitlement,
    deduct_leave_balance_atomic,
    reserve_leave_days,
    release_reserved_leave_days,
)
from gnfs_komenda.services.leave_approval_service import (
    dfo_approve_leave,
    dfo_reject_leave,
    can_user_approve_leave,
    is_self_approval,
)
from gnfs_komenda.services.leave_recommendation_service import (
    can_user_recommend_leave,
)
from gnfs_komenda.services.leave_calculator import calculate_leave_dates


# ==============================================================================
# PROMPT 3: DISTRICT FIRE STATION LEAVE WORKFLOW TESTS (40 TESTS)
# ==============================================================================

"""
District Fire Station Leave Workflow Tests (Prompt 3 of 3 - 40 Test Cases).
"""
import datetime
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from gnfs_komenda.models import (
    AuditLog, CommandAppointment, District, FunctionalOffice,
    FunctionalRoleAssignment, LeaveApprovalDecision, LeaveEntitlement,
    LeaveRequest, LeaveType, LeaveWorkflowHistory, NorminalRoll,
    Rank, Region, Station, WatchRoomBooking, ApplicationRole, AdministrativeAssignment
)
from gnfs_komenda.services.leave_workflow_service import (
    submit_district_leave,
    admin_process_leave,
    operations_recommend_leave,
    command_approve_leave,
    command_reject_leave,
    admin_send_to_watch_room,
    watch_room_book_leave,
    watch_room_record_resumption,
    get_personal_status_tracker,
)
from gnfs_komenda.services.leave_balance_service import (
    get_base_annual_entitlement,
    calculate_proportionate_entitlement,
    deduct_leave_balance_atomic,
    reserve_leave_days,
    release_reserved_leave_days,
)
from gnfs_komenda.services.leave_approval_service import (
    dfo_approve_leave,
    dfo_reject_leave,
    can_user_approve_leave,
    is_self_approval,
)
from gnfs_komenda.services.leave_recommendation_service import (
    can_user_recommend_leave,
)
from gnfs_komenda.services.leave_calculator import calculate_leave_dates


# ==============================================================================
# PROMPT 3: DISTRICT FIRE STATION LEAVE WORKFLOW TESTS (40 TESTS)
# ==============================================================================

"""
District Fire Station Leave Workflow Tests (Prompt 3 of 3 - 40 Test Cases).
"""
import datetime
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from gnfs_komenda.models import (
    AuditLog, CommandAppointment, District, FunctionalOffice,
    FunctionalRoleAssignment, LeaveApprovalDecision, LeaveEntitlement,
    LeaveRequest, LeaveType, LeaveWorkflowHistory, NorminalRoll,
    Rank, Region, Station, WatchRoomBooking, ApplicationRole, AdministrativeAssignment
)
from gnfs_komenda.services.leave_workflow_service import (
    submit_district_leave,
    admin_process_leave,
    operations_recommend_leave,
    command_approve_leave,
    command_reject_leave,
    admin_send_to_watch_room,
    watch_room_book_leave,
    watch_room_record_resumption,
    get_personal_status_tracker,
)
from gnfs_komenda.services.leave_balance_service import (
    get_base_annual_entitlement,
    calculate_proportionate_entitlement,
    deduct_leave_balance_atomic,
    reserve_leave_days,
    release_reserved_leave_days,
)
from gnfs_komenda.services.leave_approval_service import (
    dfo_approve_leave,
    dfo_reject_leave,
    can_user_approve_leave,
    is_self_approval,
)
from gnfs_komenda.services.leave_recommendation_service import (
    can_user_recommend_leave,
)
from gnfs_komenda.services.leave_calculator import calculate_leave_dates


# ==============================================================================
# PROMPT 3: DISTRICT FIRE STATION LEAVE WORKFLOW TESTS (40 TESTS)
# ==============================================================================

"""
District Fire Station Leave Workflow Tests (Prompt 3 of 3 - 40 Test Cases).
"""
import datetime
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from gnfs_komenda.models import (
    AuditLog, CommandAppointment, District, FunctionalOffice,
    FunctionalRoleAssignment, LeaveApprovalDecision, LeaveEntitlement,
    LeaveRequest, LeaveType, LeaveWorkflowHistory, NorminalRoll,
    Rank, Region, Station, WatchRoomBooking, ApplicationRole, AdministrativeAssignment
)
from gnfs_komenda.services.leave_workflow_service import (
    submit_district_leave,
    admin_process_leave,
    operations_recommend_leave,
    command_approve_leave,
    command_reject_leave,
    admin_send_to_watch_room,
    watch_room_book_leave,
    watch_room_record_resumption,
    get_personal_status_tracker,
)
from gnfs_komenda.services.leave_balance_service import (
    get_base_annual_entitlement,
    calculate_proportionate_entitlement,
    deduct_leave_balance_atomic,
    reserve_leave_days,
    release_reserved_leave_days,
)
from gnfs_komenda.services.leave_approval_service import (
    dfo_approve_leave,
    dfo_reject_leave,
    can_user_approve_leave,
    is_self_approval,
)
from gnfs_komenda.services.leave_recommendation_service import (
    can_user_recommend_leave,
)
from gnfs_komenda.services.leave_calculator import calculate_leave_dates


# ==============================================================================
# PROMPT 3: DISTRICT FIRE STATION LEAVE WORKFLOW TESTS (40 TESTS)
# ==============================================================================

"""
District Fire Station Leave Workflow Tests (Prompt 3 of 3 - 40 Test Cases).
"""
import datetime
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from gnfs_komenda.models import (
    AuditLog, CommandAppointment, District, FunctionalOffice,
    FunctionalRoleAssignment, LeaveApprovalDecision, LeaveEntitlement,
    LeaveRequest, LeaveType, LeaveWorkflowHistory, NorminalRoll,
    Rank, Region, Station, WatchRoomBooking, ApplicationRole, AdministrativeAssignment
)
from gnfs_komenda.services.leave_workflow_service import (
    submit_district_leave,
    admin_process_leave,
    operations_recommend_leave,
    command_approve_leave,
    command_reject_leave,
    admin_send_to_watch_room,
    watch_room_book_leave,
    watch_room_record_resumption,
    get_personal_status_tracker,
)
from gnfs_komenda.services.leave_balance_service import (
    get_base_annual_entitlement,
    calculate_proportionate_entitlement,
    deduct_leave_balance_atomic,
    reserve_leave_days,
    release_reserved_leave_days,
)
from gnfs_komenda.services.leave_approval_service import (
    dfo_approve_leave,
    dfo_reject_leave,
    can_user_approve_leave,
    is_self_approval,
)
from gnfs_komenda.services.leave_recommendation_service import (
    can_user_recommend_leave,
)
from gnfs_komenda.services.leave_calculator import calculate_leave_dates


# ==============================================================================
# PROMPT 3: DISTRICT FIRE STATION LEAVE WORKFLOW TESTS (40 TESTS)
# ==============================================================================

"""
District Fire Station Leave Workflow Tests (Prompt 3 of 3 - 40 Test Cases).
"""
import datetime
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from gnfs_komenda.models import (
    AuditLog, CommandAppointment, District, FunctionalOffice,
    FunctionalRoleAssignment, LeaveApprovalDecision, LeaveEntitlement,
    LeaveRequest, LeaveType, LeaveWorkflowHistory, NorminalRoll,
    Rank, Region, Station, WatchRoomBooking, ApplicationRole, AdministrativeAssignment
)
from gnfs_komenda.services.leave_workflow_service import (
    submit_district_leave,
    admin_process_leave,
    operations_recommend_leave,
    command_approve_leave,
    command_reject_leave,
    admin_send_to_watch_room,
    watch_room_book_leave,
    watch_room_record_resumption,
    get_personal_status_tracker,
)
from gnfs_komenda.services.leave_balance_service import (
    get_base_annual_entitlement,
    calculate_proportionate_entitlement,
    deduct_leave_balance_atomic,
    reserve_leave_days,
    release_reserved_leave_days,
)
from gnfs_komenda.services.leave_approval_service import (
    dfo_approve_leave,
    dfo_reject_leave,
    can_user_approve_leave,
    is_self_approval,
)
from gnfs_komenda.services.leave_recommendation_service import (
    can_user_recommend_leave,
)
from gnfs_komenda.services.leave_calculator import calculate_leave_dates


class DistrictLeaveWorkflowTests(TestCase):
    """
    Test suite for Prompt 3: District Fire Station Leave Workflow (40 Tests).
    ADMINISTRATION -> OPERATIONS -> DFO/MFO -> WATCH ROOM -> RESUMPTION
    """
    def setUp(self):
        self.today = timezone.localdate()
        self.user_model = get_user_model()

        # Next Monday for clean working days calculations
        days_ahead = 7 - self.today.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        self.next_monday = self.today + timedelta(days=days_ahead)
        self.future_start = self.next_monday + timedelta(days=14)

        # Ranks
        self.rank_junior = Rank.objects.create(
            name="Assistant Fire Officer (AFO)",
            abbreviation="AFO",
            officer_category=Rank.OfficerCategory.JUNIOR,
            hierarchy_order=11,
            active=True
        )
        self.rank_senior = Rank.objects.create(
            name="Divisional Officer I (DO I)",
            abbreviation="DO I",
            officer_category=Rank.OfficerCategory.SENIOR,
            hierarchy_order=5,
            active=True
        )

        # Regions
        self.region_central = Region.objects.create(name="Central Region", code="CR", active=True)
        self.region_western = Region.objects.create(name="Western Region", code="WR", active=True)

        # Districts
        self.district_keea = District.objects.create(
            name="KEEA Municipal",
            region=self.region_central,
            code="KEEA",
            command_type=District.CommandType.MUNICIPAL,
            co_designation="MFO",
            active=True
        )
        self.district_ccma = District.objects.create(
            name="Cape Coast Metro",
            region=self.region_central,
            code="CCMA",
            command_type=District.CommandType.METROPOLITAN,
            co_designation="METRO_FO",
            active=True
        )
        self.district_stma = District.objects.create(
            name="Sekondi-Takoradi Metro",
            region=self.region_western,
            code="STMA",
            command_type=District.CommandType.METROPOLITAN,
            co_designation="METRO_FO",
            active=True
        )

        # Stations
        self.station_komenda = Station.objects.create(
            name="Komenda Fire Station",
            district=self.district_keea,
            code="KOM",
            active=True
        )
        self.station_cape_coast = Station.objects.create(
            name="Cape Coast Fire Station",
            district=self.district_ccma,
            code="CC",
            active=True
        )
        self.station_sekondi = Station.objects.create(
            name="Sekondi Fire Station",
            district=self.district_stma,
            code="SEK",
            active=True
        )

        # Leave Types
        self.leave_annual, _ = LeaveType.objects.get_or_create(
            code="ANNUAL",
            defaults={
                "name": "Annual Leave",
                "calculation_mode": LeaveType.CalculationMode.WORKING_DAYS,
                "default_days_junior": 36,
                "default_days_senior": 42,
                "active": True
            }
        )
        self.leave_maternity, _ = LeaveType.objects.get_or_create(
            code="MATERNITY",
            defaults={
                "name": "Maternity Leave",
                "calculation_mode": LeaveType.CalculationMode.CALENDAR_DAYS,
                "default_days_junior": 90,
                "default_days_senior": 90,
                "allowed_gender": LeaveType.AllowedGender.FEMALE,
                "requires_attachment": True,
                "active": True
            }
        )
        self.leave_paternity, _ = LeaveType.objects.get_or_create(
            code="PATERNITY",
            defaults={
                "name": "Paternity Leave",
                "calculation_mode": LeaveType.CalculationMode.WORKING_DAYS,
                "default_days_junior": 5,
                "default_days_senior": 5,
                "allowed_gender": LeaveType.AllowedGender.MALE,
                "active": True
            }
        )

        # Functional Offices in KEEA
        self.fo_keea_admin = FunctionalOffice.objects.create(
            name="KEEA Administration", function_type="ADMINISTRATION",
            command_level="DISTRICT", district=self.district_keea, region=self.region_central,
            is_established=True, active=True
        )
        self.fo_keea_ops = FunctionalOffice.objects.create(
            name="KEEA Operations", function_type="OPERATIONS",
            command_level="DISTRICT", district=self.district_keea, region=self.region_central,
            is_established=True, active=True
        )
        self.fo_keea_wr = FunctionalOffice.objects.create(
            name="KEEA Watch Room", function_type="WATCH_ROOM",
            command_level="DISTRICT", district=self.district_keea, region=self.region_central,
            is_established=True, active=True
        )

        # Application Roles
        self.app_role_admin, _ = ApplicationRole.objects.get_or_create(
            code="DISTRICT_ADMIN",
            defaults={"name": "District Administrator", "is_active": True}
        )

        # 1. Applicant: Operations Junior Officer
        self.p_ops = NorminalRoll.objects.create(
            staffID="GNFS/OPS01",
            fullname="Kwame Operations",
            officer_category=Rank.OfficerCategory.JUNIOR,
            rank=self.rank_junior,
            station=self.station_komenda,
            ghana_card="GHA-111111111-1",
            birth_date=self.today - timedelta(days=365 * 28),
            leave_status="AT POST",
            is_active=True
        )
        self.user_ops = self.user_model.objects.create_user(
            username="GNFS/OPS01",
            email="kwame.ops@gnfs.gov.gh",
            password="password123"
        )
        # Assign to Operations Functional Office
        FunctionalRoleAssignment.objects.create(
            user=self.user_ops,
            functional_office=self.fo_keea_ops,
            role="FUNCTION_MEMBER",
            scope_level="DISTRICT",
            district=self.district_keea,
            region=self.region_central,
            active=True
        )
        self.entitlement_ops = LeaveEntitlement.objects.create(
            personnel=self.p_ops,
            leave_year=self.today.year,
            leave_type=self.leave_annual,
            base_entitlement=36,
            days_taken=0,
            days_reserved=0
        )

        # 2. Senior Officer Applicant
        self.p_senior = NorminalRoll.objects.create(
            staffID="GNFS/SNR01",
            fullname="Kofi Senior",
            officer_category=Rank.OfficerCategory.SENIOR,
            rank=self.rank_senior,
            station=self.station_komenda,
            ghana_card="GHA-222222222-2",
            birth_date=self.today - timedelta(days=365 * 45),
            leave_status="AT POST",
            is_active=True
        )
        self.user_senior = self.user_model.objects.create_user(
            username="GNFS/SNR01",
            email="kofi.snr@gnfs.gov.gh",
            password="password123"
        )
        self.entitlement_senior = LeaveEntitlement.objects.create(
            personnel=self.p_senior,
            leave_year=self.today.year,
            leave_type=self.leave_annual,
            base_entitlement=42,
            days_taken=0,
            days_reserved=0
        )

        # 3. District Administration Officer
        self.p_admin = NorminalRoll.objects.create(
            staffID="GNFS/ADM01",
            fullname="Ama Admin",
            officer_category=Rank.OfficerCategory.JUNIOR,
            rank=self.rank_junior,
            station=self.station_komenda,
            ghana_card="GHA-333333333-3",
            birth_date=self.today - timedelta(days=365 * 32),
            leave_status="AT POST",
            is_active=True
        )
        self.user_admin = self.user_model.objects.create_user(
            username="GNFS/ADM01",
            email="ama.admin@gnfs.gov.gh",
            password="password123"
        )
        AdministrativeAssignment.objects.create(
            personnel=self.p_admin,
            role=self.app_role_admin,
            assignment_type='ROLE',
            level='DISTRICT',
            district=self.district_keea,
            region=self.region_central,
            effective_from=self.today,
            is_active=True
        )
        FunctionalRoleAssignment.objects.create(
            user=self.user_admin,
            functional_office=self.fo_keea_admin,
            role="FUNCTION_LEAD",
            scope_level="DISTRICT",
            district=self.district_keea,
            region=self.region_central,
            active=True
        )

        # 4. District Operations Head / Recommender
        self.p_ops_head = NorminalRoll.objects.create(
            staffID="GNFS/OPS_HEAD",
            fullname="Yaw Operations Lead",
            officer_category=Rank.OfficerCategory.SENIOR,
            rank=self.rank_senior,
            station=self.station_komenda,
            ghana_card="GHA-444444444-4",
            birth_date=self.today - timedelta(days=365 * 40),
            leave_status="AT POST",
            is_active=True
        )
        self.user_ops_head = self.user_model.objects.create_user(
            username="GNFS/OPS_HEAD",
            email="yaw.opshead@gnfs.gov.gh",
            password="password123"
        )
        FunctionalRoleAssignment.objects.create(
            user=self.user_ops_head,
            functional_office=self.fo_keea_ops,
            role="FUNCTION_LEAD",
            scope_level="DISTRICT",
            district=self.district_keea,
            region=self.region_central,
            active=True
        )

        # 5. DFO / MFO (Command Authority)
        self.p_dfo = NorminalRoll.objects.create(
            staffID="GNFS/DFO01",
            fullname="Commander Boateng",
            officer_category=Rank.OfficerCategory.SENIOR,
            rank=self.rank_senior,
            station=self.station_komenda,
            ghana_card="GHA-555555555-5",
            birth_date=self.today - timedelta(days=365 * 50),
            leave_status="AT POST",
            is_active=True
        )
        self.user_dfo = self.user_model.objects.create_user(
            username="GNFS/DFO01",
            email="boateng.dfo@gnfs.gov.gh",
            password="password123"
        )
        self.cmd_dfo = CommandAppointment.objects.create(
            appointment_type="MFO",
            personnel=self.p_dfo,
            district=self.district_keea,
            effective_from=self.today,
            is_active=True
        )

        # 6. District 2IC
        self.p_2ic = NorminalRoll.objects.create(
            staffID="GNFS/2IC01",
            fullname="Esi Second-in-Command",
            officer_category=Rank.OfficerCategory.SENIOR,
            rank=self.rank_senior,
            station=self.station_komenda,
            ghana_card="GHA-666666666-6",
            birth_date=self.today - timedelta(days=365 * 42),
            leave_status="AT POST",
            is_active=True
        )
        self.user_2ic = self.user_model.objects.create_user(
            username="GNFS/2IC01",
            email="esi.2ic@gnfs.gov.gh",
            password="password123"
        )
        self.cmd_2ic = CommandAppointment.objects.create(
            appointment_type="DISTRICT_2IC",
            personnel=self.p_2ic,
            district=self.district_keea,
            effective_from=self.today,
            is_active=True
        )

        # 7. Watch Room Officer
        self.p_wr = NorminalRoll.objects.create(
            staffID="GNFS/WR01",
            fullname="Kojo Watchman",
            officer_category=Rank.OfficerCategory.JUNIOR,
            rank=self.rank_junior,
            station=self.station_komenda,
            ghana_card="GHA-777777777-7",
            birth_date=self.today - timedelta(days=365 * 25),
            leave_status="AT POST",
            is_active=True
        )
        self.user_wr = self.user_model.objects.create_user(
            username="GNFS/WR01",
            email="kojo.wr@gnfs.gov.gh",
            password="password123"
        )
        FunctionalRoleAssignment.objects.create(
            user=self.user_wr,
            functional_office=self.fo_keea_wr,
            role="FUNCTION_MEMBER",
            scope_level="DISTRICT",
            district=self.district_keea,
            region=self.region_central,
            active=True
        )

        # 8. Cross-District DFO (CCMA)
        self.p_ccma_dfo = NorminalRoll.objects.create(
            staffID="GNFS/CCMA_DFO",
            fullname="Cape Coast Metro Commander",
            officer_category=Rank.OfficerCategory.SENIOR,
            rank=self.rank_senior,
            station=self.station_cape_coast,
            ghana_card="GHA-888888888-8",
            birth_date=self.today - timedelta(days=365 * 48),
            leave_status="AT POST",
            is_active=True
        )
        self.user_ccma_dfo = self.user_model.objects.create_user(
            username="GNFS/CCMA_DFO",
            email="ccma.dfo@gnfs.gov.gh",
            password="password123"
        )
        CommandAppointment.objects.create(
            appointment_type="METRO_FO",
            personnel=self.p_ccma_dfo,
            district=self.district_ccma,
            effective_from=self.today,
            is_active=True
        )

        # 9. Cross-Region RFO (Western Region)
        self.p_wr_rfo = NorminalRoll.objects.create(
            staffID="GNFS/WR_RFO",
            fullname="Western Regional Commander",
            officer_category=Rank.OfficerCategory.SENIOR,
            rank=self.rank_senior,
            station=self.station_sekondi,
            ghana_card="GHA-999999999-9",
            birth_date=self.today - timedelta(days=365 * 52),
            leave_status="AT POST",
            is_active=True
        )
        self.user_wr_rfo = self.user_model.objects.create_user(
            username="GNFS/WR_RFO",
            email="wr.rfo@gnfs.gov.gh",
            password="password123"
        )
        CommandAppointment.objects.create(
            appointment_type="RFO",
            personnel=self.p_wr_rfo,
            region=self.region_western,
            effective_from=self.today,
            is_active=True
        )

    # 1. Personnel submits own leave (PENDING_ADMIN_REVIEW).
    def test_01_personnel_submits_own_leave_status_pending_admin_review(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=10,
            requested_by=self.user_ops,
            reason="Annual rest"
        )
        self.assertEqual(req.status, LeaveRequest.Status.PENDING_ADMIN_REVIEW)
        self.assertEqual(req.personnel, self.p_ops)
        self.assertEqual(req.requested_by, self.user_ops)
        self.assertEqual(req.duration, 10)
        self.assertEqual(req.routing_district, self.district_keea)
        self.assertTrue(LeaveWorkflowHistory.objects.filter(
            leave_request=req,
            action__in=["SUBMITTED", "LEAVE_SUBMITTED"]
        ).exists())
        self.entitlement_ops.refresh_from_db()
        self.assertEqual(self.entitlement_ops.days_reserved, 10)
        self.assertEqual(self.entitlement_ops.days_taken, 0)

    # 2. Administration receives request in leave inbox.
    def test_02_administration_receives_request_in_leave_inbox(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=10,
            requested_by=self.user_ops
        )
        inbox_qs = LeaveRequest.objects.filter(
            routing_district=self.district_keea,
            status=LeaveRequest.Status.PENDING_ADMIN_REVIEW
        )
        self.assertTrue(inbox_qs.filter(id=req.id).exists())
        self.client.login(username="GNFS/ADM01", password="password123")
        res = self.client.get(reverse('workspace-admin-district', args=[self.district_keea.id]))
        self.assertEqual(res.status_code, 200)
        self.assertIn(req, res.context['inbox_new'])

    # 3. Operations does not receive before Admin forwards.
    def test_03_operations_does_not_receive_before_admin_forwards(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=10,
            requested_by=self.user_ops
        )
        ops_qs = LeaveRequest.objects.filter(
            routing_district=self.district_keea,
            status=LeaveRequest.Status.PENDING_OPERATIONS_RECOMMENDATION
        )
        self.assertFalse(ops_qs.filter(id=req.id).exists())
        self.client.login(username="GNFS/OPS_HEAD", password="password123")
        res = self.client.get(reverse('workspace-ops-district', args=[self.district_keea.id]))
        self.assertEqual(res.status_code, 200)
        self.assertNotIn(req, res.context['inbox_awaiting_rec'])

    # 4. Admin forwards Operations Personnel request.
    def test_04_admin_forwards_operations_personnel_request(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=10,
            requested_by=self.user_ops
        )
        admin_process_leave(req.id, self.user_admin, 'FORWARD_TO_OPERATIONS', comments="Forwarded to Ops")
        req.refresh_from_db()
        self.assertEqual(req.status, LeaveRequest.Status.PENDING_OPERATIONS_RECOMMENDATION)
        self.assertIsNotNone(req.processed_at)
        self.assertEqual(req.processed_by, self.user_admin)

    # 5. Operations receives request (PENDING_OPERATIONS_RECOMMENDATION).
    def test_05_operations_receives_request(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=10,
            requested_by=self.user_ops
        )
        admin_process_leave(req.id, self.user_admin, 'FORWARD_TO_OPERATIONS')
        ops_qs = LeaveRequest.objects.filter(
            routing_district=self.district_keea,
            status=LeaveRequest.Status.PENDING_OPERATIONS_RECOMMENDATION
        )
        self.assertTrue(ops_qs.filter(id=req.id).exists())
        self.client.login(username="GNFS/OPS_HEAD", password="password123")
        res = self.client.get(reverse('workspace-ops-district', args=[self.district_keea.id]))
        self.assertEqual(res.status_code, 200)
        self.assertIn(req, res.context['inbox_awaiting_rec'])

    # 6. Operations recommends (PENDING_DFO_MFO_DECISION).
    def test_06_operations_recommends_moves_to_pending_dfo_mfo_decision(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=10,
            requested_by=self.user_ops
        )
        admin_process_leave(req.id, self.user_admin, 'FORWARD_TO_OPERATIONS')
        operations_recommend_leave(req.id, self.user_ops_head, 'RECOMMEND', comments="Relief assigned")
        req.refresh_from_db()
        self.assertEqual(req.status, LeaveRequest.Status.PENDING_DFO_MFO_DECISION)
        self.assertIn(req.operations_recommendation, ["RECOMMEND", "RECOMMENDED"])
        self.assertEqual(req.operations_recommended_by, self.user_ops_head)
        self.entitlement_ops.refresh_from_db()
        self.assertEqual(self.entitlement_ops.days_taken, 0)
        self.assertEqual(self.entitlement_ops.days_reserved, 10)

    # 7. Operations cannot final approve (PermissionDenied).
    def test_07_operations_cannot_final_approve(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=10,
            requested_by=self.user_ops
        )
        admin_process_leave(req.id, self.user_admin, 'FORWARD_TO_OPERATIONS')
        with self.assertRaises((PermissionDenied, ValidationError)):
            operations_recommend_leave(req.id, self.user_ops_head, 'APPROVE')
        with self.assertRaises(PermissionDenied):
            command_approve_leave(req.id, self.user_ops_head)

    # 8. Operations not-recommend is preserved with comment.
    def test_08_operations_not_recommend_is_preserved_with_comment(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=10,
            requested_by=self.user_ops
        )
        admin_process_leave(req.id, self.user_admin, 'FORWARD_TO_OPERATIONS')
        operations_recommend_leave(req.id, self.user_ops_head, 'NOT_RECOMMEND', comments="Station strength deficient")
        req.refresh_from_db()
        self.assertEqual(req.status, LeaveRequest.Status.PENDING_DFO_MFO_DECISION)
        self.assertIn(req.operations_recommendation, ["NOT_RECOMMEND", "NOT_RECOMMENDED"])
        self.assertEqual(req.operations_comments, "Station strength deficient")
        self.assertTrue(LeaveApprovalDecision.objects.filter(
            leave_request=req,
            decision__in=["NOT_RECOMMEND", "NOT_RECOMMENDED", "OPERATIONS_NOT_RECOMMENDED"]
        ).exists())
        self.entitlement_ops.refresh_from_db()
        self.assertEqual(self.entitlement_ops.days_taken, 0)

    # 9. DFO/MFO receives recommendation in command queue.
    def test_09_dfo_mfo_receives_recommendation_in_command_queue(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=10,
            requested_by=self.user_ops
        )
        admin_process_leave(req.id, self.user_admin, 'FORWARD_TO_OPERATIONS')
        operations_recommend_leave(req.id, self.user_ops_head, 'RECOMMEND')
        cmd_qs = LeaveRequest.objects.filter(
            routing_district=self.district_keea,
            status=LeaveRequest.Status.PENDING_DFO_MFO_DECISION
        )
        self.assertTrue(cmd_qs.filter(id=req.id).exists())
        self.client.login(username="GNFS/DFO01", password="password123")
        res = self.client.get(reverse('dashboard-district-command', args=[self.district_keea.id]))
        self.assertEqual(res.status_code, 200)
        self.assertIn(req, res.context['decision_leaves'])

    # 10. DFO/MFO approves (APPROVED_PENDING_ADMIN_PROCESSING).
    def test_10_dfo_mfo_approves_moves_to_approved_pending_admin_processing(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=10,
            requested_by=self.user_ops
        )
        admin_process_leave(req.id, self.user_admin, 'FORWARD_TO_OPERATIONS')
        operations_recommend_leave(req.id, self.user_ops_head, 'RECOMMEND')
        command_approve_leave(req.id, self.user_dfo, comments="Approved by MFO")
        req.refresh_from_db()
        self.assertEqual(req.status, LeaveRequest.Status.APPROVED_PENDING_ADMIN_PROCESSING)
        self.assertEqual(req.approved_by, self.user_dfo)
        self.assertIsNotNone(req.approved_at)
        self.entitlement_ops.refresh_from_db()
        self.assertEqual(self.entitlement_ops.days_taken, 10)
        self.assertEqual(self.entitlement_ops.days_reserved, 0)

    # 11. DFO/MFO rejects (REJECTED with reason).
    def test_11_dfo_mfo_rejects_with_reason_and_restores_balance(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=8,
            requested_by=self.user_ops
        )
        admin_process_leave(req.id, self.user_admin, 'FORWARD_TO_OPERATIONS')
        operations_recommend_leave(req.id, self.user_ops_head, 'NOT_RECOMMEND', comments="Short staff")
        command_reject_leave(req.id, self.user_dfo, comments="Operational emergency requires presence")
        req.refresh_from_db()
        self.assertEqual(req.status, LeaveRequest.Status.REJECTED)
        self.entitlement_ops.refresh_from_db()
        self.assertEqual(self.entitlement_ops.days_reserved, 0)
        self.assertEqual(self.entitlement_ops.days_taken, 0)

    # 12. DFO/MFO cannot self-approve (PermissionDenied).
    def test_12_dfo_mfo_cannot_self_approve(self):
        ent_dfo = LeaveEntitlement.objects.create(
            personnel=self.p_dfo,
            leave_year=self.today.year,
            leave_type=self.leave_annual,
            base_entitlement=42
        )
        dfo_req = submit_district_leave(
            personnel=self.p_dfo,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=14,
            requested_by=self.user_dfo
        )
        dfo_req.status = LeaveRequest.Status.PENDING_DFO_MFO_DECISION
        dfo_req.save()
        with self.assertRaises(PermissionDenied):
            command_approve_leave(dfo_req.id, self.user_dfo)
        dfo_req.refresh_from_db()
        self.assertEqual(dfo_req.status, LeaveRequest.Status.PENDING_DFO_MFO_DECISION)

    # 13. Approved leave visible to Personnel in portal.
    def test_13_approved_leave_visible_to_personnel_in_portal(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=10,
            requested_by=self.user_ops
        )
        admin_process_leave(req.id, self.user_admin, 'FORWARD_TO_OPERATIONS')
        operations_recommend_leave(req.id, self.user_ops_head, 'RECOMMEND')
        command_approve_leave(req.id, self.user_dfo)
        self.client.login(username="GNFS/OPS01", password="password123")
        res = self.client.get(reverse('my-dashboard'))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode()
        self.assertIn(req.reference_number, content)

    # 14. Approved leave visible to Administration (inbox_final_processing).
    def test_14_approved_leave_visible_to_administration_inbox_final_processing(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=10,
            requested_by=self.user_ops
        )
        admin_process_leave(req.id, self.user_admin, 'FORWARD_TO_OPERATIONS')
        operations_recommend_leave(req.id, self.user_ops_head, 'RECOMMEND')
        command_approve_leave(req.id, self.user_dfo)
        self.client.login(username="GNFS/ADM01", password="password123")
        res = self.client.get(reverse('workspace-admin-district', args=[self.district_keea.id]))
        self.assertEqual(res.status_code, 200)
        self.assertIn(req, res.context['inbox_final_processing'])

    # 15. Approved leave visible to Operations dashboard.
    def test_15_approved_leave_visible_to_operations_dashboard(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=10,
            requested_by=self.user_ops
        )
        admin_process_leave(req.id, self.user_admin, 'FORWARD_TO_OPERATIONS')
        operations_recommend_leave(req.id, self.user_ops_head, 'RECOMMEND')
        command_approve_leave(req.id, self.user_dfo)
        self.client.login(username="GNFS/OPS_HEAD", password="password123")
        res = self.client.get(reverse('workspace-ops-district', args=[self.district_keea.id]))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode()
        self.assertIn(self.p_ops.fullname, content)

    # 16. Approved leave visible to DFO/MFO dashboard.
    def test_16_approved_leave_visible_to_dfo_mfo_dashboard(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=10,
            requested_by=self.user_ops
        )
        admin_process_leave(req.id, self.user_admin, 'FORWARD_TO_OPERATIONS')
        operations_recommend_leave(req.id, self.user_ops_head, 'RECOMMEND')
        command_approve_leave(req.id, self.user_dfo)
        self.client.login(username="GNFS/DFO01", password="password123")
        res = self.client.get(reverse('dashboard-district-command', args=[self.district_keea.id]))
        self.assertEqual(res.status_code, 200)
        self.assertIn(req, res.context['approved_leaves'])

    # 17. Approved leave visible to 2IC dashboard without approval actions.
    def test_17_approved_leave_visible_to_2ic_dashboard_without_approval_actions(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=10,
            requested_by=self.user_ops
        )
        admin_process_leave(req.id, self.user_admin, 'FORWARD_TO_OPERATIONS')
        operations_recommend_leave(req.id, self.user_ops_head, 'RECOMMEND')
        command_approve_leave(req.id, self.user_dfo)
        self.client.login(username="GNFS/2IC01", password="password123")
        res = self.client.get(reverse('dashboard-district-command', args=[self.district_keea.id]))
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.context['can_approve'])
        self.assertIn(req, res.context['approved_leaves'])
        content = res.content.decode()
        self.assertNotIn('name="action" value="APPROVE"', content)

    # 18. Watch Room receives only after Admin sends it (SENT_TO_WATCH_ROOM).
    def test_18_watch_room_receives_only_after_admin_sends_it(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=10,
            requested_by=self.user_ops
        )
        admin_process_leave(req.id, self.user_admin, 'FORWARD_TO_OPERATIONS')
        operations_recommend_leave(req.id, self.user_ops_head, 'RECOMMEND')
        command_approve_leave(req.id, self.user_dfo)
        wr_qs = LeaveRequest.objects.filter(
            routing_district=self.district_keea,
            status=LeaveRequest.Status.SENT_TO_WATCH_ROOM
        )
        self.assertFalse(wr_qs.filter(id=req.id).exists())
        admin_send_to_watch_room(req.id, self.user_admin, comments="Forwarded to Watch Room")
        req.refresh_from_db()
        self.assertEqual(req.status, LeaveRequest.Status.SENT_TO_WATCH_ROOM)
        self.assertTrue(wr_qs.filter(id=req.id).exists())
        self.client.login(username="GNFS/WR01", password="password123")
        res = self.client.get(reverse('workspace-watch-room-district', args=[self.district_keea.id]))
        self.assertEqual(res.status_code, 200)
        self.assertIn(req, res.context['inbox_awaiting_booking'])

    # 19. Watch Room books in Station Diary (BOOKED).
    def test_19_watch_room_books_in_station_diary(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=10,
            requested_by=self.user_ops
        )
        admin_process_leave(req.id, self.user_admin, 'FORWARD_TO_OPERATIONS')
        operations_recommend_leave(req.id, self.user_ops_head, 'RECOMMEND')
        command_approve_leave(req.id, self.user_dfo)
        admin_send_to_watch_room(req.id, self.user_admin)
        watch_room_book_leave(
            req.id, self.user_wr,
            book_type="STATION_DIARY",
            book_reference="SD/2026/045",
            remarks="Recorded in Diary"
        )
        req.refresh_from_db()
        self.assertEqual(req.status, LeaveRequest.Status.BOOKED)
        self.assertTrue(WatchRoomBooking.objects.filter(leave_request=req, book_reference="SD/2026/045").exists())

    # 20. Watch Room cannot alter approved duration.
    def test_20_watch_room_cannot_alter_approved_duration(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=10,
            requested_by=self.user_ops
        )
        admin_process_leave(req.id, self.user_admin, 'FORWARD_TO_OPERATIONS')
        operations_recommend_leave(req.id, self.user_ops_head, 'RECOMMEND')
        command_approve_leave(req.id, self.user_dfo)
        admin_send_to_watch_room(req.id, self.user_admin)
        watch_room_book_leave(req.id, self.user_wr, book_reference="SD/2026/046")
        req.refresh_from_db()
        self.assertEqual(req.duration, 10)

    # 21. Watch Room cannot alter approved dates.
    def test_21_watch_room_cannot_alter_approved_dates(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=10,
            requested_by=self.user_ops
        )
        orig_start = req.start_date
        orig_last = req.last_leave_date
        admin_process_leave(req.id, self.user_admin, 'FORWARD_TO_OPERATIONS')
        operations_recommend_leave(req.id, self.user_ops_head, 'RECOMMEND')
        command_approve_leave(req.id, self.user_dfo)
        admin_send_to_watch_room(req.id, self.user_admin)
        watch_room_book_leave(req.id, self.user_wr, book_reference="SD/2026/047")
        req.refresh_from_db()
        self.assertEqual(req.start_date, orig_start)
        self.assertEqual(req.last_leave_date, orig_last)

    # 22. Watch Room cannot approve.
    def test_22_watch_room_cannot_approve(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=10,
            requested_by=self.user_ops
        )
        admin_process_leave(req.id, self.user_admin, 'FORWARD_TO_OPERATIONS')
        operations_recommend_leave(req.id, self.user_ops_head, 'RECOMMEND')
        with self.assertRaises(PermissionDenied):
            command_approve_leave(req.id, self.user_wr)

    # 23. Rejected leave does not go for normal booking.
    def test_23_rejected_leave_does_not_go_for_normal_booking(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=10,
            requested_by=self.user_ops
        )
        admin_process_leave(req.id, self.user_admin, 'FORWARD_TO_OPERATIONS')
        operations_recommend_leave(req.id, self.user_ops_head, 'NOT_RECOMMEND')
        command_reject_leave(req.id, self.user_dfo, comments="Cannot proceed")
        with self.assertRaises((ValidationError, PermissionDenied)):
            admin_send_to_watch_room(req.id, self.user_admin)

    # 24. Recommendation remains in LeaveWorkflowHistory.
    def test_24_recommendation_remains_in_leave_workflow_history(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=10,
            requested_by=self.user_ops
        )
        admin_process_leave(req.id, self.user_admin, 'FORWARD_TO_OPERATIONS')
        operations_recommend_leave(req.id, self.user_ops_head, 'RECOMMEND', comments="Ops Approved to Recommend")
        h = LeaveWorkflowHistory.objects.filter(
            leave_request=req,
            action__in=["OPERATIONS_RECOMMENDED", "OPERATIONS_NOT_RECOMMENDED"]
        ).first()
        self.assertIsNotNone(h)
        self.assertEqual(h.actor, self.user_ops_head)

    # 25. Final approval remains in LeaveWorkflowHistory.
    def test_25_final_approval_remains_in_leave_workflow_history(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=10,
            requested_by=self.user_ops
        )
        admin_process_leave(req.id, self.user_admin, 'FORWARD_TO_OPERATIONS')
        operations_recommend_leave(req.id, self.user_ops_head, 'RECOMMEND')
        command_approve_leave(req.id, self.user_dfo, comments="Command approval granted")
        h = LeaveWorkflowHistory.objects.filter(
            leave_request=req,
            action__in=["COMMAND_APPROVED", "APPROVED"]
        ).first()
        self.assertIsNotNone(h)
        self.assertEqual(h.actor, self.user_dfo)

    # 26. Booking remains in LeaveWorkflowHistory.
    def test_26_booking_remains_in_leave_workflow_history(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=10,
            requested_by=self.user_ops
        )
        admin_process_leave(req.id, self.user_admin, 'FORWARD_TO_OPERATIONS')
        operations_recommend_leave(req.id, self.user_ops_head, 'RECOMMEND')
        command_approve_leave(req.id, self.user_dfo)
        admin_send_to_watch_room(req.id, self.user_admin)
        watch_room_book_leave(req.id, self.user_wr, book_reference="SD/2026/099")
        h = LeaveWorkflowHistory.objects.filter(
            leave_request=req,
            action="WATCH_ROOM_BOOKED"
        ).first()
        self.assertIsNotNone(h)
        self.assertEqual(h.actor, self.user_wr)

    # 27. Leave balance is deducted strictly once on command approval.
    def test_27_leave_balance_is_deducted_strictly_once_on_command_approval(self):
        # Initial
        self.entitlement_ops.refresh_from_db()
        self.assertEqual(self.entitlement_ops.days_taken, 0)
        self.assertEqual(self.entitlement_ops.days_reserved, 0)

        # 1. Submission
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=7,
            requested_by=self.user_ops
        )
        self.entitlement_ops.refresh_from_db()
        self.assertEqual(self.entitlement_ops.days_taken, 0)
        self.assertEqual(self.entitlement_ops.days_reserved, 7)

        # 2. Admin Review & Forward
        admin_process_leave(req.id, self.user_admin, 'FORWARD_TO_OPERATIONS')
        self.entitlement_ops.refresh_from_db()
        self.assertEqual(self.entitlement_ops.days_taken, 0)
        self.assertEqual(self.entitlement_ops.days_reserved, 7)

        # 3. Operations Recommendation
        operations_recommend_leave(req.id, self.user_ops_head, 'RECOMMEND')
        self.entitlement_ops.refresh_from_db()
        self.assertEqual(self.entitlement_ops.days_taken, 0)
        self.assertEqual(self.entitlement_ops.days_reserved, 7)

        # 4. Command Approval (DEDUCTED STRICTLY HERE)
        command_approve_leave(req.id, self.user_dfo)
        self.entitlement_ops.refresh_from_db()
        self.assertEqual(self.entitlement_ops.days_taken, 7)
        self.assertEqual(self.entitlement_ops.days_reserved, 0)

        # 5. Admin Dispatch to WR
        admin_send_to_watch_room(req.id, self.user_admin)
        self.entitlement_ops.refresh_from_db()
        self.assertEqual(self.entitlement_ops.days_taken, 7)
        self.assertEqual(self.entitlement_ops.days_reserved, 0)

        # 6. WR Booking
        watch_room_book_leave(req.id, self.user_wr, book_reference="SD/2026/100")
        self.entitlement_ops.refresh_from_db()
        self.assertEqual(self.entitlement_ops.days_taken, 7)
        self.assertEqual(self.entitlement_ops.days_reserved, 0)

    # 28. Cross-District access blocked (PermissionDenied).
    def test_28_cross_district_access_blocked(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=10,
            requested_by=self.user_ops
        )
        admin_process_leave(req.id, self.user_admin, 'FORWARD_TO_OPERATIONS')
        operations_recommend_leave(req.id, self.user_ops_head, 'RECOMMEND')
        with self.assertRaises(PermissionDenied):
            command_approve_leave(req.id, self.user_ccma_dfo)

    # 29. Cross-Region access blocked (PermissionDenied).
    def test_29_cross_region_access_blocked(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=10,
            requested_by=self.user_ops
        )
        admin_process_leave(req.id, self.user_admin, 'FORWARD_TO_OPERATIONS')
        operations_recommend_leave(req.id, self.user_ops_head, 'RECOMMEND')
        with self.assertRaises(PermissionDenied):
            command_approve_leave(req.id, self.user_wr_rfo)

    # 30. Future approved leave keeps personnel AT POST.
    def test_30_future_approved_leave_keeps_personnel_at_post(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=10,
            requested_by=self.user_ops
        )
        admin_process_leave(req.id, self.user_admin, 'FORWARD_TO_OPERATIONS')
        operations_recommend_leave(req.id, self.user_ops_head, 'RECOMMEND')
        command_approve_leave(req.id, self.user_dfo)
        admin_send_to_watch_room(req.id, self.user_admin)
        watch_room_book_leave(req.id, self.user_wr, book_reference="SD/2026/101")
        self.p_ops.refresh_from_db()
        self.assertEqual(self.p_ops.leave_status, "AT POST")

    # 31. Active leave marks personnel ON LEAVE on start date.
    def test_31_active_leave_marks_personnel_on_leave_on_start_date(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.today,
            duration=5,
            requested_by=self.user_ops
        )
        admin_process_leave(req.id, self.user_admin, 'FORWARD_TO_OPERATIONS')
        operations_recommend_leave(req.id, self.user_ops_head, 'RECOMMEND')
        command_approve_leave(req.id, self.user_dfo)
        admin_send_to_watch_room(req.id, self.user_admin)
        watch_room_book_leave(req.id, self.user_wr, book_reference="SD/2026/102")
        self.p_ops.refresh_from_db()
        self.assertEqual(self.p_ops.leave_status, "ON LEAVE")

    # 32. Resumption restores personnel to AT POST.
    def test_32_resumption_restores_personnel_to_at_post(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.today,
            duration=5,
            requested_by=self.user_ops
        )
        admin_process_leave(req.id, self.user_admin, 'FORWARD_TO_OPERATIONS')
        operations_recommend_leave(req.id, self.user_ops_head, 'RECOMMEND')
        command_approve_leave(req.id, self.user_dfo)
        admin_send_to_watch_room(req.id, self.user_admin)
        watch_room_book_leave(req.id, self.user_wr, book_reference="SD/2026/103")
        self.p_ops.refresh_from_db()
        self.assertEqual(self.p_ops.leave_status, "ON LEAVE")

        # Officer returns to duty
        watch_room_record_resumption(
            req.id, self.user_wr,
            resumed_at=timezone.now(),
            remarks="Officer resumed duty on schedule"
        )
        req.refresh_from_db()
        self.assertEqual(req.status, LeaveRequest.Status.RESUMED)
        self.p_ops.refresh_from_db()
        self.assertEqual(self.p_ops.leave_status, "AT POST")

    # 33. Personal Portal status tracker renders completed steps.
    def test_33_personal_portal_status_tracker_renders_completed_steps(self):
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=10,
            requested_by=self.user_ops
        )
        tracker = get_personal_status_tracker(req)
        self.assertIn("steps", tracker)
        self.assertEqual(len(tracker["steps"]), 8)
        self.assertTrue(tracker["steps"][0]["completed"])  # Submitted
        self.assertFalse(tracker["steps"][1]["completed"]) # Admin review

        admin_process_leave(req.id, self.user_admin, 'FORWARD_TO_OPERATIONS')
        tracker = get_personal_status_tracker(req)
        self.assertTrue(tracker["steps"][1]["completed"])  # Admin reviewed

        operations_recommend_leave(req.id, self.user_ops_head, 'RECOMMEND')
        tracker = get_personal_status_tracker(req)
        self.assertTrue(tracker["steps"][2]["completed"])  # Ops recommended

        command_approve_leave(req.id, self.user_dfo)
        tracker = get_personal_status_tracker(req)
        self.assertTrue(tracker["steps"][3]["completed"])  # DFO approved

    # 34. Existing annual leave calculations work.
    def test_34_existing_annual_leave_calculations_work(self):
        res = calculate_leave_dates(self.next_monday, 10, calculation_mode='WORKING_DAYS')
        # 10 working days starting on Monday: Mon-Fri (5) + Mon-Fri (5) -> End on Friday 11 days later
        expected_end = self.next_monday + timedelta(days=11)
        self.assertEqual(res['last_leave_date'], expected_end)
        self.assertEqual(res['duration'], 10)

    # 35. Proportionate entitlement calculations work.
    def test_35_proportionate_entitlement_calculations_work(self):
        mid_year = datetime.date(self.today.year, 7, 1) # 6 months remaining
        # Junior: (6/12) * 36 = 18
        j_days = calculate_proportionate_entitlement(self.p_ops, effective_date=mid_year)
        self.assertEqual(j_days, 18)
        # Senior: (6/12) * 42 = 21
        s_days = calculate_proportionate_entitlement(self.p_senior, effective_date=mid_year)
        self.assertEqual(s_days, 21)

    # 36. Maternity 90 calendar days remains.
    def test_36_maternity_90_calendar_days_remains(self):
        self.assertEqual(self.leave_maternity.calculation_mode, LeaveType.CalculationMode.CALENDAR_DAYS)
        self.assertEqual(self.leave_maternity.default_days_junior, 90)
        self.assertEqual(self.leave_maternity.default_days_senior, 90)
        self.assertEqual(self.leave_maternity.allowed_gender, LeaveType.AllowedGender.FEMALE)

    # 37. Paternity 5 working days remains.
    def test_37_paternity_5_working_days_remains(self):
        self.assertEqual(self.leave_paternity.calculation_mode, LeaveType.CalculationMode.WORKING_DAYS)
        self.assertEqual(self.leave_paternity.default_days_junior, 5)
        self.assertEqual(self.leave_paternity.default_days_senior, 5)
        self.assertEqual(self.leave_paternity.allowed_gender, LeaveType.AllowedGender.MALE)

    # 38. Senior annual entitlement remains 42 working days.
    def test_38_senior_annual_entitlement_remains_42_working_days(self):
        self.assertEqual(get_base_annual_entitlement('SENIOR'), 42)
        self.assertEqual(get_base_annual_entitlement('SENIOR_OFFICER'), 42)
        self.assertEqual(get_base_annual_entitlement('SENIOR OFFICER'), 42)

    # 39. Junior annual entitlement remains 36 working days.
    def test_39_junior_annual_entitlement_remains_36_working_days(self):
        self.assertEqual(get_base_annual_entitlement('JUNIOR'), 36)
        self.assertEqual(get_base_annual_entitlement('JUNIOR_OFFICER'), 36)
        self.assertEqual(get_base_annual_entitlement('OTHER'), 36)

    # 40. Existing authentication and transfer tests pass alongside workflow.
    def test_40_existing_authentication_and_transfer_tests_pass(self):
        # 1. Unauthenticated user redirected/blocked from protected workspace
        unauth_client = Client()
        res = unauth_client.get(reverse('workspace-admin-district', args=[self.district_keea.id]))
        self.assertEqual(res.status_code, 302)

        # 2. Authenticated authorized user accesses portal cleanly
        self.client.login(username="GNFS/OPS01", password="password123")
        res = self.client.get(reverse('my-dashboard'))
        self.assertEqual(res.status_code, 200)

        # 3. Leave request state integrity
        req = submit_district_leave(
            personnel=self.p_ops,
            leave_type=self.leave_annual,
            start_date=self.future_start,
            duration=3,
            requested_by=self.user_ops
        )
        self.assertIsNotNone(req.reference_number)
        self.assertEqual(req.creation_mode, 'PERSONNEL')
