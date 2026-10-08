from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from staff.models import UserAssignment, GNFSUserRole, StaffTOTPDevice
from gnfs_komenda.models import (
	NorminalRoll, Rank, Region, District, Station, FunctionalUnit,
	CommandAppointment, PersonnelPosting
)
from gnfs_komenda.services.command_appointment_service import (
	appoint_dfo, appoint_mfo, appoint_rfo, end_command_appointment
)
from gnfs_komenda.services.functional_assignment_service import assign_functional_unit
from gnfs_komenda.services.permission_resolver import get_available_workspaces
from staff.services.account_provisioning_service import (
	provision_personnel_account, bulk_provision_personnel_accounts,
	reset_personnel_account, get_temporary_password
)


class SeedAdminCommandTests(TestCase):
	def test_seed_admin_is_idempotent_without_resetting_password(self):
		call_command(
			"seed_admin",
			username="admin",
			password="first-password",
		)
		call_command(
			"seed_admin",
			username="admin",
			password="second-password",
		)

		user = get_user_model().objects.get(username="admin")
		self.assertEqual(
			get_user_model().objects.filter(username="admin").count(),
			1,
		)
		self.assertTrue(user.check_password("first-password"))
		self.assertTrue(user.is_active)
		self.assertFalse(user.is_staff)
		self.assertFalse(user.is_superuser)

	def test_seed_admin_can_reset_an_existing_password(self):
		user_model = get_user_model()
		user_model.objects.create_user(username="admin", password="old-password")

		call_command(
			"seed_admin",
			username="admin",
			password="new-password",
			reset_password=True,
		)

		user = user_model.objects.get(username="admin")
		self.assertTrue(user.check_password("new-password"))
		self.assertTrue(user.is_active)
		self.assertFalse(user.is_staff)
		self.assertFalse(user.is_superuser)

	def test_seed_admin_can_create_superuser(self):
		call_command(
			"seed_admin",
			username="nanagyachie@live.com",
			password="@Jay0697",
			superuser=True,
		)

		user = get_user_model().objects.get(username="nanagyachie@live.com")
		self.assertTrue(user.check_password("@Jay0697"))
		self.assertTrue(user.is_active)
		self.assertTrue(user.is_staff)
		self.assertTrue(user.is_superuser)
		self.assertEqual(user.email, "nanagyachie@live.com")

	def test_seed_admin_can_promote_existing_user_to_superuser(self):
		user_model = get_user_model()
		user_model.objects.create_user(username="admin", password="old-password")

		call_command(
			"seed_admin",
			username="admin",
			password="new-password",
			reset_password=True,
			superuser=True,
		)

		user = user_model.objects.get(username="admin")
		self.assertTrue(user.check_password("new-password"))
		self.assertTrue(user.is_active)
		self.assertTrue(user.is_staff)
		self.assertTrue(user.is_superuser)


class TOTPAndForgotPasswordTests(TestCase):
	def setUp(self):
		self.user_model = get_user_model()
		self.user = self.user_model.objects.create_user(
			username="firefighter",
			email="firefighter@example.com",
			password="InitialPassword123!",
		)

	def test_totp_device_model_creation_and_verification(self):
		import pyotp
		from staff.models import StaffTOTPDevice

		device = StaffTOTPDevice.objects.create(user=self.user)
		self.assertFalse(device.is_confirmed)
		self.assertTrue(bool(device.secret))

		uri = device.get_provisioning_uri()
		self.assertIn("otpauth://totp/", uri)

		qr_svg = device.get_qr_code_svg()
		self.assertIn("<svg", qr_svg)

		valid_token = pyotp.TOTP(device.secret).now()
		self.assertTrue(device.verify_token(valid_token))
		self.assertFalse(device.verify_token("000000" if valid_token != "000000" else "111111"))

	def test_totp_setup_view_confirm_and_disable(self):
		import pyotp
		from django.urls import reverse
		from staff.models import StaffTOTPDevice

		self.client.force_login(self.user)

		# GET generates unconfirmed device
		response = self.client.get(reverse("totp-setup"))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Two-Factor Authentication")
		self.assertContains(response, "<svg")

		device = StaffTOTPDevice.objects.get(user=self.user)
		self.assertFalse(device.is_confirmed)

		# POST valid token confirms device
		valid_token = pyotp.TOTP(device.secret).now()
		response = self.client.post(reverse("totp-setup"), {"token": valid_token})
		self.assertRedirects(response, reverse("totp-setup"))

		device.refresh_from_db()
		self.assertTrue(device.is_confirmed)

		# POST disable removes confirmed status and returns to unconfirmed setup
		response = self.client.post(reverse("totp-disable"))
		self.assertRedirects(response, reverse("totp-setup"))
		device = StaffTOTPDevice.objects.get(user=self.user)
		self.assertFalse(device.is_confirmed)


	def test_forgot_password_logs_out_authenticated_user_instead_of_redirecting(self):
		from django.urls import reverse

		# User is logged in
		self.client.force_login(self.user)

		# Accessing forgot-password should display the form, not redirect to personnel
		response = self.client.get(reverse("forgot-password"))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Forgot Password")
		self.assertFalse(response.context["user"].is_authenticated)

	def test_forgot_password_requires_enabled_totp(self):
		from django.urls import reverse

		# Requesting reset without TOTP enabled should fail
		response = self.client.post(
			reverse("forgot-password"),
			{"identity": self.user.username},
		)
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Google Authenticator (TOTP) is not enabled for this account")

	def test_forgot_password_full_workflow_with_totp(self):
		import pyotp
		from django.urls import reverse
		from staff.models import StaffTOTPDevice

		device = StaffTOTPDevice.objects.create(user=self.user, is_confirmed=True)

		# Step 1: Submit username
		response = self.client.post(
			reverse("forgot-password"),
			{"identity": self.user.username},
		)
		self.assertRedirects(response, reverse("forgot-password-verify"))

		# Step 2: Verify with wrong code fails
		response = self.client.post(
			reverse("forgot-password-verify"),
			{"token": "999999"},
		)
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Invalid or expired 6-digit code")

		# Step 2: Verify with correct TOTP code succeeds
		valid_token = pyotp.TOTP(device.secret).now()
		response = self.client.post(
			reverse("forgot-password-verify"),
			{"token": valid_token},
		)
		self.assertRedirects(response, reverse("forgot-password-reset"))

		# Step 3: Set new password
		response = self.client.post(
			reverse("forgot-password-reset"),
			{
				"new_password1": "BrandNewSecretPassword99!",
				"new_password2": "BrandNewSecretPassword99!",
			},
		)
		self.assertRedirects(response, reverse("login-staff"))

		# Check that new password works
		self.user.refresh_from_db()
		self.assertTrue(self.user.check_password("BrandNewSecretPassword99!"))


class ProfileUpdateTests(TestCase):
	def setUp(self):
		self.user_model = get_user_model()
		self.user = self.user_model.objects.create_user(
			username="johndoe",
			first_name="John",
			last_name="Doe",
			email="johndoe@example.com",
			password="InitialPassword123!",
		)

	def test_profile_view_requires_login(self):
		from django.urls import reverse
		response = self.client.get(reverse("profile"))
		self.assertEqual(response.status_code, 302)

	def test_profile_view_loads_authenticated_user_data(self):
		from django.urls import reverse
		self.client.force_login(self.user)
		response = self.client.get(reverse("profile"))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Staff Profile &amp; Settings")
		self.assertContains(response, "johndoe@example.com")
		self.assertContains(response, "John")
		self.assertContains(response, "Doe")

	def test_profile_update_details_successfully(self):
		from django.urls import reverse
		self.client.force_login(self.user)
		response = self.client.post(
			reverse("profile"),
			{
				"action": "update_profile",
				"first_name": "Johnny",
				"last_name": "Doey",
				"username": "johnnydoe",
				"email": "johnny@example.com",
			},
		)
		self.assertRedirects(response, reverse("profile"))

		self.user.refresh_from_db()
		self.assertEqual(self.user.first_name, "Johnny")
		self.assertEqual(self.user.last_name, "Doey")
		self.assertEqual(self.user.username, "johnnydoe")
		self.assertEqual(self.user.email, "johnny@example.com")

	def test_profile_update_rejects_duplicate_username(self):
		from django.urls import reverse
		self.user_model.objects.create_user(username="existinguser", password="Password123!")

		self.client.force_login(self.user)
		response = self.client.post(
			reverse("profile"),
			{
				"action": "update_profile",
				"first_name": "John",
				"last_name": "Doe",
				"username": "existinguser",
				"email": "johndoe@example.com",
			},
		)
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "This username is already taken")

	def test_profile_change_password_successfully(self):
		from django.urls import reverse
		self.client.force_login(self.user)
		response = self.client.post(
			reverse("profile"),
			{
				"action": "change_password",
				"old_password": "InitialPassword123!",
				"new_password1": "MyUpdatedPassword456!",
				"new_password2": "MyUpdatedPassword456!",
			},
		)
		self.assertRedirects(response, reverse("profile"))

		self.user.refresh_from_db()
		self.assertTrue(self.user.check_password("MyUpdatedPassword456!"))


class GNFSPersonnelAccountAndAuthenticationRuleTests(TestCase):
	def setUp(self):
		self.user_model = get_user_model()
		self.today = timezone.localdate()

		# Seeded System Super Admin (Bootstrap account)
		call_command(
			"seed_admin",
			username="nanagyachie@live.com",
			password="SuperAdminSecurePassword2026!",
			superuser=True,
		)
		self.super_admin = self.user_model.objects.get(username="nanagyachie@live.com")
		self.totp_device = StaffTOTPDevice.objects.create(
			user=self.super_admin,
			secret="JBSWY3DPEHPK3PXP",
			is_confirmed=True
		)

		# Organisational Setup
		self.rank_sfm, _ = Rank.objects.get_or_create(
			name="Senior Fireman",
			defaults={"abbreviation": "SFM", "officer_category": Rank.OfficerCategory.JUNIOR, "hierarchy_order": 10}
		)
		self.rank_acfo, _ = Rank.objects.get_or_create(
			name="Assistant Chief Fire Officer",
			defaults={"abbreviation": "ACFO", "officer_category": Rank.OfficerCategory.SENIOR, "hierarchy_order": 1}
		)
		self.region_cr = Region.objects.create(name="Central Region", code="CR")
		self.region_wr = Region.objects.create(name="Western Region", code="WR")
		self.dist_keea = District.objects.create(name="KEEA", code="KEEA", region=self.region_cr)
		self.dist_ccm = District.objects.create(name="Cape Coast Metro", code="CCM", region=self.region_cr, co_designation="MFO")
		self.station_komenda = Station.objects.create(name="Komenda Fire Station", code="KFS", district=self.dist_keea)
		self.station_cc = Station.objects.create(name="Cape Coast Station", code="CCS", district=self.dist_ccm)

		# Personnel Records
		self.p1 = NorminalRoll.objects.create(
			staffID="GNFS-10001",
			fullname="Kofi Mensah",
			rank=self.rank_sfm,
			job_grade="SFM",
			station=self.station_komenda,
			officer_category="JUNIOR",
			birth_date=self.today - timedelta(days=365 * 28),
			ghana_card="GHA-100010001-1",
			bank="GCB",
			account_number="100010001",
			mgmt_units="KEEA",
			payroll_status="ACTIVE",
			gender="MALE",
			is_active=True
		)
		PersonnelPosting.objects.create(
			personnel=self.p1, region=self.region_cr, district=self.dist_keea,
			station=self.station_komenda, is_current=True, start_date=self.today
		)

		self.p_dfo = NorminalRoll.objects.create(
			staffID="GNFS-10002",
			fullname="Kwesi DFO",
			rank=self.rank_acfo,
			job_grade="ACFO",
			station=self.station_komenda,
			officer_category="SENIOR",
			birth_date=self.today - timedelta(days=365 * 45),
			ghana_card="GHA-100020002-2",
			bank="GCB",
			account_number="100020002",
			mgmt_units="KEEA",
			payroll_status="ACTIVE",
			gender="MALE",
			is_active=True
		)
		PersonnelPosting.objects.create(
			personnel=self.p_dfo, region=self.region_cr, district=self.dist_keea,
			station=self.station_komenda, is_current=True, start_date=self.today
		)

		self.p_mfo = NorminalRoll.objects.create(
			staffID="GNFS-10003",
			fullname="Ama MFO",
			rank=self.rank_acfo,
			job_grade="ACFO",
			station=self.station_cc,
			officer_category="SENIOR",
			birth_date=self.today - timedelta(days=365 * 42),
			ghana_card="GHA-100030003-3",
			bank="GCB",
			account_number="100030003",
			mgmt_units="CCM",
			payroll_status="ACTIVE",
			gender="FEMALE",
			is_active=True
		)
		PersonnelPosting.objects.create(
			personnel=self.p_mfo, region=self.region_cr, district=self.dist_ccm,
			station=self.station_cc, is_current=True, start_date=self.today
		)

		self.p_rfo = NorminalRoll.objects.create(
			staffID="GNFS-10004",
			fullname="Yaw RFO",
			rank=self.rank_acfo,
			job_grade="ACFO",
			station=self.station_cc,
			officer_category="SENIOR",
			birth_date=self.today - timedelta(days=365 * 50),
			ghana_card="GHA-100040004-4",
			bank="GCB",
			account_number="100040004",
			mgmt_units="CR",
			payroll_status="ACTIVE",
			gender="MALE",
			is_active=True
		)
		PersonnelPosting.objects.create(
			personnel=self.p_rfo, region=self.region_cr, district=self.dist_ccm,
			station=self.station_cc, is_current=True, start_date=self.today
		)

	# 1. Existing seeded Super Admin remains unchanged
	def test_01_existing_seeded_super_admin_remains_unchanged(self):
		initial_pw_hash = self.super_admin.password
		call_command("provision_personnel_accounts")
		self.super_admin.refresh_from_db()
		self.assertEqual(self.super_admin.password, initial_pw_hash)
		self.assertTrue(self.super_admin.is_superuser)
		self.assertTrue(self.super_admin.is_staff)

	# 2. Personnel provisioning does not change Super Admin password
	def test_02_personnel_provisioning_does_not_change_super_admin_password(self):
		call_command("provision_personnel_accounts")
		self.super_admin.refresh_from_db()
		self.assertTrue(self.super_admin.check_password("SuperAdminSecurePassword2026!"))
		temp_pw = get_temporary_password()
		self.assertFalse(self.super_admin.check_password(temp_pw))

	# 3. Personnel provisioning does not change Super Admin TOTP
	def test_03_personnel_provisioning_does_not_change_super_admin_totp(self):
		call_command("provision_personnel_accounts")
		totp = StaffTOTPDevice.objects.get(user=self.super_admin)
		self.assertEqual(totp.secret, "JBSWY3DPEHPK3PXP")
		self.assertTrue(totp.is_confirmed)

	# 4. Personnel provisioning does not link Super Admin accidentally
	def test_04_personnel_provisioning_does_not_link_super_admin_accidentally(self):
		call_command("provision_personnel_accounts")
		self.super_admin.refresh_from_db()
		asgn = getattr(self.super_admin, 'assignment', None)
		self.assertIsNotNone(asgn)
		self.assertIsNone(asgn.personnel)

	# 5. Existing Admin seeder still works independently
	def test_05_existing_admin_seeder_still_works_independently(self):
		call_command(
			"seed_admin",
			username="independent_bootstrap_admin",
			password="IndependentPassword123!",
			superuser=True
		)
		user = self.user_model.objects.filter(username="independent_bootstrap_admin").first()
		self.assertIsNotNone(user)
		self.assertTrue(user.is_superuser)
		self.assertTrue(user.check_password("IndependentPassword123!"))

	# 6. Staff ID login works for personnel-linked users
	def test_06_staff_id_login_works_for_personnel_linked_users(self):
		call_command("provision_personnel_accounts")
		res = self.client.post(
			reverse("login-staff"),
			{"username": "GNFS-10001", "password": "@Jay0697"}
		)
		# First login redirects to mandatory password change
		self.assertRedirects(res, reverse("mandatory-password-change"))

	# 7. Temporary personnel password is hashed
	def test_07_temporary_personnel_password_is_hashed(self):
		user, created, _ = provision_personnel_account(self.p1)
		self.assertTrue(created)
		self.assertNotEqual(user.password, "@Jay0697")
		self.assertTrue(user.password.startswith("pbkdf2_sha256$"))

	# 8. First login requires password change
	def test_08_first_login_requires_password_change(self):
		user, _, _ = provision_personnel_account(self.p1)
		self.assertTrue(user.assignment.must_change_password)

	# 9. Protected pages cannot bypass required password change
	def test_09_protected_pages_cannot_bypass_required_password_change(self):
		user, _, _ = provision_personnel_account(self.p1)
		self.client.force_login(user)
		# Trying to access dashboard directly
		res = self.client.get(reverse("my-dashboard"))
		self.assertRedirects(res, reverse("mandatory-password-change"))

		# Trying to access profile directly
		res = self.client.get(reverse("profile"))
		self.assertRedirects(res, reverse("mandatory-password-change"))

	# 10. New private password works after change
	def test_10_new_private_password_works_after_change(self):
		user, _, _ = provision_personnel_account(self.p1)
		self.client.force_login(user)
		res = self.client.post(
			reverse("mandatory-password-change"),
			{"new_password1": "MySecretNewPassword#2026", "new_password2": "MySecretNewPassword#2026"}
		)
		self.assertRedirects(res, reverse("my-dashboard"))
		user.refresh_from_db()
		self.assertFalse(user.assignment.must_change_password)
		self.assertTrue(user.check_password("MySecretNewPassword#2026"))

	# 11. Temporary password stops working after change
	def test_11_temporary_password_stops_working_after_change(self):
		user, _, _ = provision_personnel_account(self.p1)
		self.client.force_login(user)
		self.client.post(
			reverse("mandatory-password-change"),
			{"new_password1": "MySecretNewPassword#2026", "new_password2": "MySecretNewPassword#2026"}
		)
		self.client.logout()
		# Attempting login with temporary password must fail
		res = self.client.post(
			reverse("login-staff"),
			{"username": "GNFS-10001", "password": "@Jay0697"}
		)
		self.assertEqual(res.status_code, 200) # Re-renders login form with error
		self.assertContains(res, "Invalid Staff ID / username or password")

	# 12. Existing personnel passwords are not overwritten
	def test_12_existing_personnel_passwords_are_not_overwritten(self):
		user, _, _ = provision_personnel_account(self.p1)
		user.set_password("PreExistingPrivatePassword#1")
		user.assignment.must_change_password = False
		user.assignment.save()
		user.save()

		# Run provisioning
		call_command("provision_personnel_accounts")
		user.refresh_from_db()
		self.assertTrue(user.check_password("PreExistingPrivatePassword#1"))
		self.assertFalse(user.assignment.must_change_password)

	# 13. Existing TOTP secrets are not overwritten
	def test_13_existing_totp_secrets_are_not_overwritten(self):
		user, _, _ = provision_personnel_account(self.p1)
		totp = StaffTOTPDevice.objects.create(user=user, secret="MYPERSISTENTSECRET", is_confirmed=True)
		call_command("provision_personnel_accounts")
		totp.refresh_from_db()
		self.assertEqual(totp.secret, "MYPERSISTENTSECRET")

	# 14. Re-running provisioning creates no duplicates
	def test_14_re_running_provisioning_creates_no_duplicates(self):
		res1 = bulk_provision_personnel_accounts()
		self.assertEqual(res1["created"], 4)
		users_count_before = self.user_model.objects.count()

		res2 = bulk_provision_personnel_accounts()
		self.assertEqual(res2["created"], 0)
		self.assertEqual(res2["already_linked"], 4)
		self.assertEqual(self.user_model.objects.count(), users_count_before)

	# 15. One Personnel maps to one User
	def test_15_one_personnel_maps_to_one_user(self):
		user, _, _ = provision_personnel_account(self.p1)
		self.assertEqual(self.p1.user_account, user)

	# 16. One User cannot accidentally map to multiple Personnel
	def test_16_one_user_cannot_accidentally_map_to_multiple_personnel(self):
		user, _, _ = provision_personnel_account(self.p1)
		with self.assertRaises(IntegrityError):
			UserAssignment.objects.create(user=user, personnel=self.p_dfo)

	# 17. DFO uses their normal Staff ID account
	def test_17_dfo_uses_their_normal_staff_id_account(self):
		user, _, _ = provision_personnel_account(self.p_dfo)
		appoint_dfo(district=self.dist_keea, personnel=self.p_dfo, appointed_by=self.super_admin)
		workspaces = get_available_workspaces(user)
		ws_ids = [w["id"] for w in workspaces]
		self.assertIn("my_dashboard", ws_ids)
		self.assertIn(f"cmd_dist_{self.dist_keea.id}", ws_ids)
		self.assertEqual(user.username, "GNFS-10002")

	# 18. MFO uses their normal Staff ID account
	def test_18_mfo_uses_their_normal_staff_id_account(self):
		user, _, _ = provision_personnel_account(self.p_mfo)
		appoint_mfo(district=self.dist_ccm, personnel=self.p_mfo, appointed_by=self.super_admin)
		workspaces = get_available_workspaces(user)
		ws_ids = [w["id"] for w in workspaces]
		self.assertIn("my_dashboard", ws_ids)
		self.assertIn(f"cmd_dist_{self.dist_ccm.id}", ws_ids)
		self.assertEqual(user.username, "GNFS-10003")

	# 19. RFO uses their normal Staff ID account
	def test_19_rfo_uses_their_normal_staff_id_account(self):
		user, _, _ = provision_personnel_account(self.p_rfo)
		appoint_rfo(region=self.region_cr, personnel=self.p_rfo, appointed_by=self.super_admin)
		workspaces = get_available_workspaces(user)
		ws_ids = [w["id"] for w in workspaces]
		self.assertIn("my_dashboard", ws_ids)
		self.assertIn(f"cmd_reg_{self.region_cr.id}", ws_ids)
		self.assertEqual(user.username, "GNFS-10004")

	# 20. District Admin uses their normal Staff ID account
	def test_20_district_admin_uses_their_normal_staff_id_account(self):
		user, _, _ = provision_personnel_account(self.p1)
		user.assignment.role = GNFSUserRole.DISTRICT_ADMIN
		user.assignment.district = self.dist_keea
		user.assignment.save()
		workspaces = get_available_workspaces(user)
		ws_ids = [w["id"] for w in workspaces]
		self.assertIn("my_dashboard", ws_ids)
		self.assertIn(f"cmd_dist_{self.dist_keea.id}", ws_ids)

	# 21. Regional Admin uses their normal Staff ID account
	def test_21_regional_admin_uses_their_normal_staff_id_account(self):
		user, _, _ = provision_personnel_account(self.p1)
		user.assignment.role = GNFSUserRole.REGIONAL_ADMIN
		user.assignment.region = self.region_cr
		user.assignment.save()
		workspaces = get_available_workspaces(user)
		ws_ids = [w["id"] for w in workspaces]
		self.assertIn("my_dashboard", ws_ids)
		self.assertIn(f"cmd_reg_{self.region_cr.id}", ws_ids)

	# 22. National Admin remains distinct from seeded Super Admin
	def test_22_national_admin_remains_distinct_from_seeded_super_admin(self):
		user, _, _ = provision_personnel_account(self.p1)
		user.assignment.role = GNFSUserRole.NATIONAL_ADMIN
		user.assignment.save()
		# National admin has a personnel record and is not a superuser
		self.assertFalse(user.is_superuser)
		self.assertIsNotNone(user.assignment.personnel)
		# Seeded super admin has no personnel record and is a superuser
		self.assertTrue(self.super_admin.is_superuser)
		self.assertIsNone(self.super_admin.assignment.personnel)

	# 23. Multiple roles do not create multiple accounts
	def test_23_multiple_roles_do_not_create_multiple_accounts(self):
		user, _, _ = provision_personnel_account(self.p_dfo)
		# DFO appointment
		appoint_dfo(district=self.dist_keea, personnel=self.p_dfo, appointed_by=self.super_admin)
		# Functional Directorate assignment
		func_unit = FunctionalUnit.objects.create(name="Operations Directorate", code="OPS-DIR")
		assign_functional_unit(personnel=self.p_dfo, functional_unit=func_unit, assignment_role="Director", assigned_by=self.super_admin)
		# Officer still only has 1 account
		self.assertEqual(self.user_model.objects.filter(username="GNFS-10002").count(), 1)
		self.assertEqual(self.p_dfo.user_account, user)

	# 24. Appointment changes do not reset passwords
	def test_24_appointment_changes_do_not_reset_passwords(self):
		user, _, _ = provision_personnel_account(self.p_dfo)
		user.set_password("MyPrivateDfoPassword123!")
		user.save()
		apt = appoint_dfo(district=self.dist_keea, personnel=self.p_dfo, appointed_by=self.super_admin)
		end_command_appointment(apt, user=self.super_admin, remarks="End of tenure")
		user.refresh_from_db()
		self.assertTrue(user.check_password("MyPrivateDfoPassword123!"))

	# 25. Promotions do not reset passwords
	def test_25_promotions_do_not_reset_passwords(self):
		user, _, _ = provision_personnel_account(self.p1)
		user.set_password("PersonnelPass123!")
		user.save()
		self.p1.rank = self.rank_acfo
		self.p1.job_grade = "ACFO"
		self.p1.save()
		user.refresh_from_db()
		self.assertTrue(user.check_password("PersonnelPass123!"))

	# 26. Transfers do not create new accounts
	def test_26_transfers_do_not_create_new_accounts(self):
		user, _, _ = provision_personnel_account(self.p1)
		user_count_before = self.user_model.objects.count()
		# Update posting to Western Region
		stn_wr = Station.objects.create(name="Sekondi Fire Station", code="SKS", district=District.objects.create(name="STMA", code="STMA", region=self.region_wr))
		PersonnelPosting.objects.create(
			personnel=self.p1, region=self.region_wr, district=stn_wr.district,
			station=stn_wr, is_current=True, start_date=self.today
		)
		self.assertEqual(self.user_model.objects.count(), user_count_before)
		self.assertEqual(self.p1.user_account, user)

	# 27. Ending a management appointment removes its workspace
	def test_27_ending_a_management_appointment_removes_its_workspace(self):
		user, _, _ = provision_personnel_account(self.p_dfo)
		apt = appoint_dfo(district=self.dist_keea, personnel=self.p_dfo, appointed_by=self.super_admin)
		self.assertIn(f"cmd_dist_{self.dist_keea.id}", [w["id"] for w in get_available_workspaces(user)])
		end_command_appointment(apt, user=self.super_admin, remarks="Relieved of command")
		self.assertNotIn(f"cmd_dist_{self.dist_keea.id}", [w["id"] for w in get_available_workspaces(user)])

	# 28. Ending a management appointment does not remove My Dashboard
	def test_28_ending_a_management_appointment_does_not_remove_my_dashboard(self):
		user, _, _ = provision_personnel_account(self.p_dfo)
		apt = appoint_dfo(district=self.dist_keea, personnel=self.p_dfo, appointed_by=self.super_admin)
		end_command_appointment(apt, user=self.super_admin, remarks="Relieved of command")
		self.assertIn("my_dashboard", [w["id"] for w in get_available_workspaces(user)])

	# 29. Account reset preserves roles and personnel data
	def test_29_account_reset_preserves_roles_and_personnel_data(self):
		user, _, _ = provision_personnel_account(self.p1)
		user.set_password("OldPrivatePassword#1")
		user.assignment.must_change_password = False
		user.assignment.role = GNFSUserRole.DISTRICT_ADMIN
		user.assignment.district = self.dist_keea
		user.assignment.save()
		user.save()

		reset_personnel_account(self.p1, requested_by=self.super_admin)
		user.refresh_from_db()
		temp_pw = get_temporary_password()
		self.assertTrue(user.check_password(temp_pw))
		self.assertTrue(user.assignment.must_change_password)
		self.assertEqual(user.assignment.role, GNFSUserRole.DISTRICT_ADMIN)
		self.assertEqual(user.assignment.district, self.dist_keea)
		self.assertEqual(self.p1.fullname, "Kofi Mensah")

	# 30. Personnel administrators cannot reset protected system accounts
	def test_30_personnel_administrators_cannot_reset_protected_system_accounts(self):
		admin_asgn = getattr(self.super_admin, 'assignment', None)
		admin_asgn.is_system_account = True
		admin_asgn.save()
		# Create fake personnel pointing to super admin
		dummy_p = NorminalRoll.objects.create(
			staffID="SYS-99999",
			fullname="System Admin Officer",
			station=self.station_komenda,
			birth_date=self.today - timedelta(days=365 * 30),
			ghana_card="GHA-999999999-9",
			bank="GCB",
			account_number="999999999",
			mgmt_units="HQ",
			payroll_status="ACTIVE",
			gender="MALE",
			is_active=True
		)
		admin_asgn.personnel = dummy_p
		admin_asgn.save()

		# Regular district admin tries to reset super admin
		dist_admin = self.user_model.objects.create_user(username="dist_admin", password="password123")
		UserAssignment.objects.create(user=dist_admin, role=GNFSUserRole.DISTRICT_ADMIN, district=self.dist_keea)

		with self.assertRaises(PermissionDenied):
			reset_personnel_account(dummy_p, requested_by=dist_admin)




