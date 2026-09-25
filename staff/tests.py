from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase


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



