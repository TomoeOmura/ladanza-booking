import base64
import unittest
from copy import deepcopy
from datetime import datetime, timedelta
from unittest.mock import patch

import rsa
from google.auth import crypt
import main
import ticket_integration as integration


class TicketIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.public, cls.private = rsa.newkeys(2048)
        cls.verifier = crypt.RSAVerifier.from_string(cls.public.save_pkcs1())

    def setUp(self):
        self.now = datetime(2026, 10, 24, 12, tzinfo=integration.JST)
        self.key_patch = patch.object(integration, "verifier", return_value=self.verifier)
        self.key_patch.start()
        self.addCleanup(self.key_patch.stop)

    def headers(self, now, path=integration.INTEGRATION_PATH):
        timestamp = str(int(now.timestamp()))
        signature = rsa.sign(f"GET\n{path}\n{timestamp}".encode(), self.private, "SHA-256")
        return {"X-LaDanza-Timestamp": timestamp,
                "X-LaDanza-Signature": base64.urlsafe_b64encode(signature).decode().rstrip("=")}

    def event(self, event_id="one", name="試験 甲", phone_hash="test-phone-hash", **private):
        return {"id": event_id, "description": f"お名前: {name}\n電話: PRIVATE",
                "start": {"dateTime": "2026-10-24T10:00:00+09:00"},
                "extendedProperties": {"private": {"source": "ladanza-booking", "phone_hash": phone_hash,
                    "token_hash": "SECRET", "capacity": "10", **private}}}

    def test_signatures_expiry_and_path_scope(self):
        self.assertTrue(integration.authorized(self.headers(self.now), self.now))
        for headers in ({}, self.headers(self.now-timedelta(seconds=61)),
                        self.headers(self.now+timedelta(seconds=11)), self.headers(self.now, "/api/admin/settings")):
            self.assertFalse(integration.authorized(headers, self.now))
        bad = self.headers(self.now)
        bad["X-LaDanza-Signature"] = "A"*342
        self.assertFalse(integration.authorized(bad, self.now))

    def test_deduplicates_people_not_bookings_and_excludes_cancelled_and_manual(self):
        events = [self.event(), self.event("two", name="試験　甲"), self.event("three", name="試験 乙"),
                  self.event("cancel", status="cancelled"), self.event("manual", source="manual"),
                  self.event("settings", source="ladanza-booking-settings")]
        deleted = self.event("deleted"); deleted["status"] = "cancelled"
        transparent = self.event("transparent"); transparent["transparency"] = "transparent"
        events.extend([deleted, transparent, deepcopy(events[0])])
        result = integration.daily_totals(events, self.now)
        self.assertEqual((result["people"], result["reservations"], result["attendance"]), (2, 3, 3))
        self.assertEqual(set(result), {"date", "timezone", "source", "updated_at", "people", "reservations", "attendance", "unidentified_reservations"})
        self.assertNotIn("PRIVATE", str(result)); self.assertNotIn("SECRET", str(result)); self.assertNotIn("試験", str(result))

    def test_japan_day_boundaries(self):
        events=[]
        for i,start in enumerate(["2026-10-23T14:59:59Z", "2026-10-23T15:00:00Z", "2026-10-24T14:59:59Z", "2026-10-24T15:00:00Z"]):
            event=self.event(str(i)); event["start"]["dateTime"]=start; events.append(event)
        result=integration.daily_totals(events,self.now)
        self.assertEqual(result["reservations"],2)
        self.assertEqual(result["people"],1)

    def test_missing_identity_and_legacy_group_count_are_not_guessed(self):
        for event in (self.event(phone_hash=""), self.event(participants="3")):
            result=integration.daily_totals([event],self.now)
            self.assertIsNone(result["people"])
            self.assertEqual(result["unidentified_reservations"],1)
        self.assertEqual(integration.daily_totals([self.event(participants="3")],self.now)["attendance"],3)
        self.assertIsNone(integration.daily_totals([self.event(participants="unknown")],self.now)["attendance"])
        self.assertEqual(integration.daily_totals([],self.now)["people"],0)

    def test_endpoint_auth_and_upstream_failure(self):
        client=main.app.test_client()
        with patch.object(main,"list_calendar_events") as events:
            denied=client.get(integration.INTEGRATION_PATH)
            self.assertEqual(denied.status_code,401);events.assert_not_called()
            self.assertIn("no-store",denied.headers["Cache-Control"])
        headers=self.headers(datetime.now(integration.JST))
        with patch.object(main,"calendar_for",return_value="test"), patch.object(main,"list_calendar_events",return_value=[]):
            result=client.get(integration.INTEGRATION_PATH,headers=headers)
            self.assertEqual(result.status_code,200);self.assertEqual(result.json["people"],0)
            self.assertIn("no-store",result.headers["Cache-Control"])
        with patch.object(main,"calendar_for",side_effect=RuntimeError("unavailable")):
            failed=client.get(integration.INTEGRATION_PATH,headers=headers)
            self.assertEqual(failed.status_code,503);self.assertNotIn("people",failed.json)
        # Integration signatures do not grant admin settings access.
        self.assertEqual(client.get("/api/admin/settings",headers=headers).status_code,401)


if __name__ == "__main__":
    unittest.main()
