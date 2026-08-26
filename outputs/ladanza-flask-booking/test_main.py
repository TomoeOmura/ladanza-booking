import os
import unittest
from copy import deepcopy
from datetime import datetime, timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

os.environ["GOOGLE_CALENDAR_ID"] = "shared-calendar@example.com"
os.environ["PUBLIC_URL"] = "https://booking.example.com"
os.environ["ADMIN_TOKEN"] = "test-admin-password-that-is-not-used-in-production"
os.environ["TOKEN_SECRET"] = "test-token-secret-with-more-than-32-characters"

import main  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")


class BookingApiTests(unittest.TestCase):
    def setUp(self):
        self.events = {}
        self.saved_settings = deepcopy(main.DEFAULT_SETTINGS)
        main._settings_cache = None
        main._settings_cache_until = None
        self.patches = [
            patch.object(main, "calendar_for", return_value="shared-calendar@example.com"),
            patch.object(main, "load_calendar_settings", side_effect=lambda _calendar: deepcopy(self.saved_settings)),
            patch.object(main, "save_calendar_settings", side_effect=self._save_settings),
            patch.object(main, "list_calendar_events", side_effect=self._list_events),
            patch.object(main, "get_event", side_effect=lambda _calendar, event_id: deepcopy(self.events.get(event_id))),
            patch.object(main, "create_event", side_effect=self._create_event),
            patch.object(main, "cancel_booking_event", side_effect=self._cancel_event),
            patch.object(main, "test_calendar_connection", return_value={"connected": True}),
        ]
        for item in self.patches:
            item.start()
        self.client = main.app.test_client()

    def tearDown(self):
        patch.stopall()

    def _save_settings(self, _calendar, settings):
        self.saved_settings = deepcopy(settings)
        return {"id": "settings"}

    def _list_events(self, _calendar, start, end):
        result = []
        for event in self.events.values():
            event_start, event_end = main.event_window(event)
            if main.overlaps(start, end, event_start, event_end):
                result.append(deepcopy(event))
        return result

    def _create_event(self, _calendar, start, end, booking, event_id=None):
        private = {
            "source": "ladanza-booking", "instructor": booking["instructor"],
            "menu": booking["menu"], "starts_at": start.isoformat(),
            "booking_kind": "group" if int(booking["capacity"]) > 1 else "private",
            "booking_id": booking["booking_id"], "request_key_hash": booking["request_key_hash"],
            "token_hash": booking["token_hash"], "duration": str(booking["duration"]),
            "capacity": str(booking["capacity"]), "privacy_consent_at": booking["privacy_consent_at"],
            "booking_source": booking["source"], "status": "confirmed",
        }
        event = {"id": event_id, "summary": "test",
                 "start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()},
                 "extendedProperties": {"private": private}}
        self.events[event_id] = deepcopy(event)
        return event

    def _cancel_event(self, _calendar, event):
        stored = self.events[event["id"]]
        stored.setdefault("extendedProperties", {}).setdefault("private", {})["status"] = "cancelled"
        stored["transparency"] = "transparent"
        return deepcopy(stored)

    def _future_start(self, hour=10, minute=0):
        target = datetime.now(JST).date() + timedelta(days=1)
        while target.weekday() == 6:
            target += timedelta(days=1)
        return datetime(target.year, target.month, target.day, hour, minute, tzinfo=JST).isoformat()

    def _payload(self, *, instructor="大村 尊", menu="個人レッスン 30分", request_key=None, start=None):
        return {"menu": menu, "instructor": instructor, "starts_at": start or self._future_start(),
                "name": "予約 テスト", "phone": "070-3148-7791", "email": "guest@example.com",
                "privacy_consent": True, "source": "website",
                "request_key": request_key or f"request-key-{os.urandom(16).hex()}"}

    def test_public_pages_and_health(self):
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.get_json()["storage"], "google-calendar")
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        response.close()
        privacy = self.client.get("/privacy")
        self.assertEqual(privacy.status_code, 200)
        privacy.close()
        self.assertNotIn("demoSlots", page)
        self.assertIn("架空の空き枠は表示していません", page)

    def test_privacy_consent_is_required(self):
        payload = self._payload()
        payload["privacy_consent"] = False
        response = self.client.post("/api/bookings", json=payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("同意", response.get_json()["detail"])

    def test_same_instructor_is_blocked_but_different_instructor_is_allowed(self):
        start = self._future_start()
        first = self.client.post("/api/bookings", json=self._payload(
            instructor="大村 尊", menu="個人レッスン 60分", start=start))
        self.assertEqual(first.status_code, 201)
        blocked = self.client.post("/api/bookings", json=self._payload(
            instructor="大村 尊", menu="個人レッスン 30分", start=start))
        self.assertEqual(blocked.status_code, 409)
        parallel = self.client.post("/api/bookings", json=self._payload(
            instructor="大村 友恵", menu="個人レッスン 30分", start=start))
        self.assertEqual(parallel.status_code, 201)

    def test_repeated_submission_returns_the_same_booking(self):
        payload = self._payload(request_key="same-request-key-12345678901234567890")
        first = self.client.post("/api/bookings", json=payload)
        second = self.client.post("/api/bookings", json=payload)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.get_json()["id"], second.get_json()["id"])
        self.assertEqual(len(self.events), 1)

    def test_group_capacity_is_enforced(self):
        self.saved_settings["menus"]["サロン・グループ"]["capacity"] = 2
        main._settings_cache = None
        start = self._future_start()
        for _ in range(2):
            response = self.client.post("/api/bookings", json=self._payload(
                instructor="スタジオ主催", menu="サロン・グループ", start=start))
            self.assertEqual(response.status_code, 201)
        full = self.client.post("/api/bookings", json=self._payload(
            instructor="スタジオ主催", menu="サロン・グループ", start=start))
        self.assertEqual(full.status_code, 409)
        self.assertIn("満員", full.get_json()["detail"])

    def test_confirmation_and_cancellation_survive_without_sqlite(self):
        created = self.client.post("/api/bookings", json=self._payload()).get_json()
        token = parse_qs(urlparse(created["reservation_url"]).query)["token"][0]
        stored = self.events[created["id"]]["extendedProperties"]["private"]
        self.assertNotEqual(stored["token_hash"], token)
        self.assertEqual(len(stored["token_hash"]), 64)
        self.assertEqual(self.client.get(f"/api/reservations/{token}").status_code, 200)
        self.assertEqual(self.client.post(f"/api/reservations/{token}/cancel").status_code, 200)
        self.assertEqual(self.client.get(f"/api/reservations/{token}").get_json()["status"], "cancelled")

    def test_admin_settings_are_saved_to_calendar(self):
        headers = {"X-Admin-Token": os.environ["ADMIN_TOKEN"]}
        response = self.client.get("/api/admin/settings", headers=headers)
        self.assertEqual(response.status_code, 200)
        settings = response.get_json()
        settings["menus"]["サロン・グループ"]["capacity"] = 12
        saved = self.client.put("/api/admin/settings", headers=headers, json=settings)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(self.saved_settings["menus"]["サロン・グループ"]["capacity"], 12)

    def test_admin_has_no_default_password(self):
        self.assertEqual(self.client.get("/api/admin/settings").status_code, 401)
        page = self.client.get("/admin")
        self.assertNotIn("デモは admin", page.get_data(as_text=True))
        page.close()


if __name__ == "__main__":
    unittest.main()
