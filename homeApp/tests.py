from datetime import date, timedelta

from django.contrib.auth.models import Group, User
from django.core import mail
from django.test import override_settings
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
import time
from homeApp.auth_services import get_password_reset_email, sync_profile_password, token_hash
from homeApp.models import AccessLink, EmailVerification, SchoolDetail, SchoolOwner, SchoolSession
from managementApp.models import Student


class SchoolLicenseMiddlewareTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner_group = Group.objects.create(name="Owner")
        self.user = User.objects.create_user(username="owner", password="pass12345")
        self.user.groups.add(self.owner_group)
        self.owner = SchoolOwner.objects.get(userID=self.user)
        SchoolOwner.objects.filter(pk=self.owner.pk).update(
            name="Owner One",
            username="owner",
        )
        self.owner.refresh_from_db()
        self.school = SchoolDetail.objects.create(
            ownerID=self.owner,
            schoolName="Sunrise School",
            address="Main road",
        )
        self.session = SchoolSession.objects.create(
            schoolID=self.school,
            sessionYear="2026-2027",
            isCurrent=True,
        )

    def _login_with_session(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["current_session"] = {
            "Id": self.session.id,
            "SchoolID": self.school.id,
            "currentSessionYear": self.session.sessionYear,
        }
        session["session_list"] = [
            {
                "Id": self.session.id,
                "SchoolID": self.school.id,
                "currentSessionYear": self.session.sessionYear,
            }
        ]
        session.save()

    def test_management_dashboard_always_shows_license_validation(self):
        self._login_with_session()
        response = self.client.get(reverse("managementApp:admin_home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "License Validation")
        self.assertContains(response, "Active")

    def test_expired_school_blocks_feature_page_but_not_dashboard(self):
        self.school.activationEndDate = date.today() - timedelta(days=1)
        self.school.save(update_fields=["activationEndDate", "lastUpdatedOn"])
        self._login_with_session()

        dashboard_response = self.client.get(reverse("managementApp:admin_home"))
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertContains(dashboard_response, "Expired")

        blocked_response = self.client.get(reverse("managementApp:manage_class"))
        self.assertEqual(blocked_response.status_code, 403)
        self.assertContains(blocked_response, "Access expired", status_code=403)
        self.assertContains(blocked_response, "Activation expired", status_code=403)

    def test_expired_school_blocks_management_api(self):
        expired_on = date.today() - timedelta(days=1)
        self.school.activationEndDate = expired_on
        self.school.save(update_fields=["activationEndDate", "lastUpdatedOn"])
        self._login_with_session()

        response = self.client.get(reverse("managementAppAPI:get_school_detail_api"))
        self.assertEqual(response.status_code, 403)
        payload = response.json()
        self.assertEqual(payload["success"], False)
        self.assertEqual(payload["message"], "Activation expired. Please contact the administrator to renew your school license.")
        self.assertEqual(payload["license"]["status"], "expired")
        self.assertEqual(payload["license"]["valid_until"], str(expired_on))
        self.assertEqual(payload["license"]["school_name"], "Sunrise School")


class AuthRecoveryServiceTests(TestCase):
    def test_password_reset_email_prefers_linked_student_profile_email(self):
        user = User.objects.create_user(username="student1", email="user@example.com", password="pass12345")
        student = Student.objects.create(userID=user, name="Student One", email="student@example.com")

        email, role, profile = get_password_reset_email(user)

        self.assertEqual(email, "student@example.com")
        self.assertEqual(role, "student")
        self.assertEqual(profile, student)

    def test_password_reset_email_falls_back_to_user_email(self):
        user = User.objects.create_user(username="student2", email="user@example.com", password="pass12345")
        Student.objects.create(userID=user, name="Student Two", email="")

        email, role, profile = get_password_reset_email(user)

        self.assertEqual(email, "user@example.com")
        self.assertEqual(role, "student")
        self.assertIsNotNone(profile)

    def test_sync_profile_password_updates_linked_student_record(self):
        user = User.objects.create_user(username="student3", email="user@example.com", password="pass12345")
        student = Student.objects.create(userID=user, name="Student Three", email="student@example.com", password="old")

        sync_profile_password(user, "newpass123")

        student.refresh_from_db()
        self.assertEqual(student.password, "newpass123")

    def test_update_email_updates_user_and_linked_student_profile(self):
        user = User.objects.create_user(username="student_email", email="", password="pass12345")
        student = Student.objects.create(userID=user, name="Student Email", email="")
        self.client.force_login(user)

        response = self.client.post(reverse("homeApp:update_email"), {"email": "student@example.com"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["success"], True)
        user.refresh_from_db()
        student.refresh_from_db()
        self.assertEqual(user.email, "student@example.com")
        self.assertEqual(student.email, "student@example.com")

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="no-reply@example.com",
    )
    def test_update_email_sends_verification_email(self):
        user = User.objects.create_user(username="verify_email_user", email="", password="pass12345")
        Student.objects.create(userID=user, name="Verify Email", email="")
        self.client.force_login(user)

        response = self.client.post(reverse("homeApp:update_email"), {"email": "verify@example.com"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["verificationSent"], True)
        for _ in range(20):
            if mail.outbox:
                break
            time.sleep(0.05)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Verify your SchoolsStack email address", mail.outbox[0].subject)
        self.assertIn("Verify Email Address", mail.outbox[0].alternatives[0][0])

    def test_update_email_rejects_invalid_email(self):
        user = User.objects.create_user(username="student_bad_email", email="", password="pass12345")
        self.client.force_login(user)

        response = self.client.post(reverse("homeApp:update_email"), {"email": "not-an-email"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["success"], False)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="no-reply@example.com",
    )
    def test_forgot_password_does_not_send_to_unverified_email(self):
        user = User.objects.create_user(username="student_reset", email="", password="pass12345")
        Student.objects.create(userID=user, name="Student Reset", email="student-reset@example.com")

        response = self.client.post(reverse("homeApp:send_password_reset_link"), {"userName": "student_reset"})

        self.assertEqual(response.status_code, 302)
        time.sleep(0.1)
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="no-reply@example.com",
    )
    def test_forgot_password_sends_html_email_to_verified_email_in_background(self):
        user = User.objects.create_user(username="student_reset_verified", email="", password="pass12345")
        Student.objects.create(userID=user, name="Student Reset", email="student-reset-verified@example.com")
        EmailVerification.objects.create(
            userID=user,
            email="student-reset-verified@example.com",
            tokenHash=token_hash("verified"),
            expiresAt=timezone.now() + timedelta(hours=1),
            verifiedAt=timezone.now(),
        )

        response = self.client.post(reverse("homeApp:send_password_reset_link"), {"userName": "student_reset_verified"})

        self.assertEqual(response.status_code, 302)
        for _ in range(20):
            if mail.outbox:
                break
            time.sleep(0.05)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["student-reset-verified@example.com"])
        self.assertIn("Reset your SchoolsStack password", message.subject)
        self.assertIn("Spam or Junk", message.body)
        self.assertTrue(message.alternatives)
        html_body, mime_type = message.alternatives[0]
        self.assertEqual(mime_type, "text/html")
        self.assertIn("Set New Password", html_body)

    def test_verify_email_marks_verification_as_verified(self):
        user = User.objects.create_user(username="verify_link_user", email="verify-link@example.com", password="pass12345")
        token = "email-verify-token"
        verification = EmailVerification.objects.create(
            userID=user,
            email="verify-link@example.com",
            tokenHash=token_hash(token),
            expiresAt=timezone.now() + timedelta(hours=1),
        )

        response = self.client.get(reverse("homeApp:verify_email", kwargs={"token": token}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Email verified")
        verification.refresh_from_db()
        self.assertIsNotNone(verification.verifiedAt)

    def test_profile_shows_verified_email_badge(self):
        user = User.objects.create_user(username="profile_verified", email="", password="pass12345")
        student_group, created = Group.objects.get_or_create(name="Student")
        user.groups.add(student_group)
        Student.objects.create(userID=user, name="Profile Verified", email="profile-verified@example.com")
        EmailVerification.objects.create(
            userID=user,
            email="profile-verified@example.com",
            tokenHash=token_hash("profile-verified"),
            expiresAt=timezone.now() + timedelta(hours=1),
            verifiedAt=timezone.now(),
        )
        self.client.force_login(user)

        response = self.client.get(reverse("homeApp:profile_page"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "profile-verified@example.com")
        self.assertContains(response, "Verified")

    def test_access_link_get_shows_password_setup_without_login(self):
        user = User.objects.create_user(username="student4", email="user@example.com", password="oldpass123")
        Student.objects.create(userID=user, name="Student Four", email="student@example.com", password="oldpass123")
        token = "setup-token"
        AccessLink.objects.create(
            userID=user,
            purpose="student_quick_login",
            tokenHash=token_hash(token),
            expiresAt=timezone.now() + timedelta(hours=1),
        )

        response = self.client.get(reverse("homeApp:access_link_login", kwargs={"token": token}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Set your password")
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_access_link_post_sets_password_and_consumes_link(self):
        user = User.objects.create_user(username="student5", email="user@example.com", password="oldpass123")
        student = Student.objects.create(userID=user, name="Student Five", email="student@example.com", password="oldpass123")
        token = "setup-token-post"
        link = AccessLink.objects.create(
            userID=user,
            purpose="student_quick_login",
            tokenHash=token_hash(token),
            expiresAt=timezone.now() + timedelta(hours=1),
        )

        response = self.client.post(
            reverse("homeApp:access_link_login", kwargs={"token": token}),
            {"new_password": "newpass12345", "confirm_password": "newpass12345"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Password set")
        user.refresh_from_db()
        student.refresh_from_db()
        link.refresh_from_db()
        self.assertTrue(user.check_password("newpass12345"))
        self.assertEqual(student.password, "newpass12345")
        self.assertEqual(link.usedCount, 1)
        self.assertIsNotNone(link.usedAt)
        self.assertFalse(response.wsgi_request.user.is_authenticated)
