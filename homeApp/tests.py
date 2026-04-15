from datetime import date, timedelta

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse
from homeApp.models import SchoolDetail, SchoolOwner, SchoolSession


class SchoolLicenseMiddlewareTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner_group = Group.objects.create(name="Owner")
        self.user = User.objects.create_user(username="owner", password="pass12345")
        self.user.groups.add(self.owner_group)
        self.owner = SchoolOwner.objects.create(
            name="Owner One",
            userID=self.user,
            username="owner",
        )
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
        self.assertContains(blocked_response, "Feature access is temporarily locked", status_code=403)
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
