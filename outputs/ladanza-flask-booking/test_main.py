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
            patch.object(main, "find_booking_by_number", side_effect=self._find_booking),
            patch.object(main, "find_bookings_by_phone_hash", side_effect=self._find_bookings_by_phone_hash),
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

    def _find_booking(self, _calendar, booking_id):
        for event in self.events.values():
            if event.get("extendedProperties", {}).get("private", {}).get("booking_id") == booking_id:
                return deepcopy(event)
        return None

    def _find_bookings_by_phone_hash(self, _calendar, phone_hash, *, upcoming_only=True):
        result = []
        for event in self.events.values():
            private = event.get("extendedProperties", {}).get("private", {})
            if private.get("phone_hash") == phone_hash:
                if upcoming_only and main.event_window(event)[0] < datetime.now(JST):
                    continue
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
            "booking_source": booking["source"], "email_hash": booking.get("email_hash", ""),
            "phone_hash": booking["phone_hash"], "status": "confirmed",
        }
        event = {"id": event_id, "summary": "test",
                 "description": f"電話: {booking['phone']}",
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

    def _payload(self, *, instructor="大村 尊", menu="個人レッスン 30分", request_key=None, start=None,
                 phone="070-3148-7791"):
        return {"menu": menu, "instructor": instructor, "starts_at": start or self._future_start(),
                "name": "予約 テスト", "phone": phone,
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
        lookup = self.client.get("/reservation-lookup")
        self.assertEqual(lookup.status_code, 200)
        self.assertIn("すべての予約を確認する", lookup.get_data(as_text=True))
        lookup.close()
        self.assertNotIn("demoSlots", page)
        self.assertIn("架空の空き枠は表示していません", page)
        self.assertNotIn('name="email"', page)
        self.assertNotIn("メールアドレス", page)
        self.assertIn("days:'31'", page)
        self.assertIn('id="datePicker"', page)
        self.assertIn('id="timePicker" class="hidden"', page)
        self.assertIn("日付を選び直す", page)
        self.assertIn("LINE友だち追加特典：20分無料体験（お1人様1回）", page)
        self.assertIn("trialOffer=source==='line'&&params.get('trial')==='1'", page)
        self.assertIn("const publicMenus=['個人レッスン 60分','個人レッスン 30分','初心者パック 30分','初級パック 30分','サロン','チャーター 30分']", page)
        self.assertIn("const menus=trialOffer?[...publicMenus,'無料体験 20分']:publicMenus", page)
        self.assertIn("const menuLabels={'チャーター 30分':'チャーター'}", page)

    def test_slots_default_to_a_31_day_window(self):
        response = self.client.get("/api/slots", query_string={
            "menu": "個人レッスン 30分", "instructor": "大村 尊"
        })
        self.assertEqual(response.status_code, 200)
        slots = response.get_json()
        self.assertTrue(slots)
        last_date = max(datetime.fromisoformat(item["starts_at"]).date() for item in slots)
        self.assertGreaterEqual(last_date, datetime.now(JST).date() + timedelta(days=29))

    def test_privacy_consent_is_required(self):
        payload = self._payload()
        payload["privacy_consent"] = False
        response = self.client.post("/api/bookings", json=payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("同意", response.get_json()["detail"])

    def test_free_trial_is_limited_to_once_per_phone(self):
        first = self.client.post("/api/bookings", json=self._payload(
            menu="無料体験 20分", start=self._future_start(10)))
        self.assertEqual(first.status_code, 201)
        repeated = self.client.post("/api/bookings", json=self._payload(
            menu="無料体験 20分", start=self._future_start(11)))
        self.assertEqual(repeated.status_code, 409)
        self.assertIn("お1人様1回", repeated.get_json()["detail"])
        other_phone = self.client.post("/api/bookings", json=self._payload(
            menu="無料体験 20分", start=self._future_start(11), phone="070-9999-9999"))
        self.assertEqual(other_phone.status_code, 201)

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
        self.saved_settings["menus"]["サロン"]["capacity"] = 2
        main._settings_cache = None
        start = self._future_start()
        for _ in range(2):
            response = self.client.post("/api/bookings", json=self._payload(
                instructor="スタジオ主催", menu="サロン", start=start))
            self.assertEqual(response.status_code, 201)
        full = self.client.post("/api/bookings", json=self._payload(
            instructor="スタジオ主催", menu="サロン", start=start))
        self.assertEqual(full.status_code, 409)
        self.assertIn("満員", full.get_json()["detail"])

    def test_charter_uses_studio_schedule_and_capacity_six(self):
        start = self._future_start()
        for index in range(6):
            response = self.client.post("/api/bookings", json=self._payload(
                instructor="スタジオ主催", menu="チャーター 30分", start=start,
                phone=f"070-0000-{index:04d}"))
            self.assertEqual(response.status_code, 201)
        full = self.client.post("/api/bookings", json=self._payload(
            instructor="スタジオ主催", menu="チャーター 30分", start=start,
            phone="070-0000-9999"))
        self.assertEqual(full.status_code, 409)
        self.assertIn("満員", full.get_json()["detail"])
        wrong_instructor = self.client.post("/api/bookings", json=self._payload(
            instructor="大村 尊", menu="チャーター 30分", start=self._future_start(11)))
        self.assertEqual(wrong_instructor.status_code, 400)

    def test_confirmation_and_cancellation_survive_without_sqlite(self):
        created = self.client.post("/api/bookings", json=self._payload()).get_json()
        token = parse_qs(urlparse(created["reservation_url"]).query)["token"][0]
        stored = self.events[created["id"]]["extendedProperties"]["private"]
        self.assertNotEqual(stored["token_hash"], token)
        self.assertEqual(len(stored["token_hash"]), 64)
        self.assertEqual(self.client.get(f"/api/reservations/{token}").status_code, 200)
        self.assertEqual(self.client.post(f"/api/reservations/{token}/cancel").status_code, 200)
        self.assertEqual(self.client.get(f"/api/reservations/{token}").get_json()["status"], "cancelled")

    def test_tampered_reservation_token_is_rejected(self):
        created = self.client.post("/api/bookings", json=self._payload()).get_json()
        token = parse_qs(urlparse(created["reservation_url"]).query)["token"][0]
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        self.assertEqual(self.client.get(f"/api/reservations/{tampered}").status_code, 404)

    def test_lookup_by_reservation_number_and_contact(self):
        created = self.client.post("/api/bookings", json=self._payload()).get_json()
        payload = {"reservation_number": created["reservation_number"], "contact": "070-3148-7791"}
        found = self.client.post("/api/reservations/lookup", json=payload)
        self.assertEqual(found.status_code, 200)
        self.assertEqual(found.get_json()["reservation_number"], created["reservation_number"])
        self.assertEqual(self.client.post("/api/reservations/lookup", json={
            "reservation_number": created["reservation_number"], "contact": "070-0000-0000"
        }).status_code, 404)
        cancelled = self.client.post("/api/reservations/lookup/cancel", json={
            "reservation_number": created["reservation_number"], "contact": "070-3148-7791"
        })
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(self.events[created["id"]]["extendedProperties"]["private"]["status"], "cancelled")

    def test_lookup_all_upcoming_reservations_by_phone(self):
        first = self.client.post("/api/bookings", json=self._payload(start=self._future_start(10))).get_json()
        second = self.client.post("/api/bookings", json=self._payload(start=self._future_start(11))).get_json()
        self.client.post("/api/bookings", json=self._payload(
            start=self._future_start(12), phone="070-9999-9999"))
        response = self.client.post("/api/reservations/lookup-all", json={"contact": "070-3148-7791"})
        self.assertEqual(response.status_code, 200)
        bookings = response.get_json()["bookings"]
        self.assertEqual([item["reservation_number"] for item in bookings], [
            first["reservation_number"], second["reservation_number"]
        ])
        self.assertEqual(self.client.post("/api/reservations/lookup-all", json={
            "contact": "070-0000-0000"
        }).status_code, 404)

    def test_admin_settings_are_saved_to_calendar(self):
        headers = {"X-Admin-Token": os.environ["ADMIN_TOKEN"]}
        response = self.client.get("/api/admin/settings", headers=headers)
        self.assertEqual(response.status_code, 200)
        settings = response.get_json()
        settings["menus"]["サロン"]["capacity"] = 9
        saved = self.client.put("/api/admin/settings", headers=headers, json=settings)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(self.saved_settings["menus"]["サロン"]["capacity"], 9)

    def test_legacy_menu_settings_are_migrated(self):
        legacy = deepcopy(main.DEFAULT_SETTINGS)
        legacy["menus"].pop("初級パック 30分")
        legacy["menus"].pop("サロン")
        legacy["menus"].pop("チャーター 30分")
        legacy["menus"]["初級パック30分"] = {"duration": 30, "capacity": 1}
        legacy["menus"]["サロン・グループ"] = {"duration": 30, "capacity": 15}
        migrated = main.normalize_settings(legacy)
        self.assertEqual(migrated["menus"]["初級パック 30分"]["duration"], 30)
        self.assertEqual(migrated["menus"]["サロン"]["capacity"], 10)
        self.assertEqual(migrated["menus"]["チャーター 30分"]["capacity"], 6)
        self.assertNotIn("サロン・グループ", migrated["menus"])

    def test_admin_has_no_default_password(self):
        self.assertEqual(self.client.get("/api/admin/settings").status_code, 401)
        page = self.client.get("/admin")
        self.assertNotIn("デモは admin", page.get_data(as_text=True))
        page.close()


if __name__ == "__main__":
    unittest.main()
