import unittest
from datetime import datetime
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo

import google_calendar


JST = ZoneInfo("Asia/Tokyo")


class _Executable:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class _Events:
    def __init__(self, items):
        self.items = items

    def list(self, **kwargs):
        return _Executable({"items": self.items})


class _Service:
    def __init__(self, items):
        self._events = _Events(items)

    def events(self):
        return self._events


def event(start, end, *, instructor=None, menu=None, kind=None):
    private = {}
    if instructor:
        private["instructor"] = instructor
    if menu:
        private["menu"] = menu
    if kind:
        private["booking_kind"] = kind
    private["starts_at"] = start
    value = {
        "status": "confirmed",
        "start": {"dateTime": start},
        "end": {"dateTime": end},
    }
    if private:
        value["extendedProperties"] = {"private": private}
    return value


class SharedCalendarTests(unittest.TestCase):
    def test_contact_correction_uses_revision_and_preserves_booking_fields(self):
        original = {"id": "booking", "etag": '"v1"', "description": "お名前: テスト\n電話: 09000000000\n備考: 保持",
                    "extendedProperties": {"private": {"source": "ladanza-booking", "status": "cancelled", "token_hash": "original", "capacity": "10"}}}
        service = MagicMock()
        operation = service.events.return_value.patch.return_value
        operation.headers = {}
        with patch.object(google_calendar, "service", return_value=service):
            google_calendar.update_booking_contact("calendar", original, "09012345678", "new-phone", "new-recovery")
        args = service.events.return_value.patch.call_args.kwargs
        self.assertEqual(operation.headers["If-Match"], '"v1"')
        self.assertEqual(set(args["body"]), {"description", "extendedProperties"})
        self.assertIn("備考: 保持", args["body"]["description"])
        self.assertNotIn("09000000000", args["body"]["description"])
        self.assertIn("電話: 09012345678", args["body"]["description"])
        private = args["body"]["extendedProperties"]["private"]
        self.assertEqual(private["token_hash"], "original")
        self.assertEqual(private["status"], "cancelled")
        self.assertEqual(original["extendedProperties"]["private"]["capacity"], private["capacity"])

    def test_cancellation_cannot_overwrite_concurrent_contact_repair(self):
        service = MagicMock()
        operation = service.events.return_value.patch.return_value
        operation.headers = {}
        with patch.object(google_calendar, "service", return_value=service):
            google_calendar.cancel_booking_event("calendar", {"id": "booking", "etag": '"before-repair"'})
        self.assertEqual(operation.headers["If-Match"], '"before-repair"')

    def test_other_instructor_event_does_not_block(self):
        items = [
            event("2030-01-07T10:00:00+09:00", "2030-01-07T10:30:00+09:00", instructor="大村 尊"),
            event("2030-01-07T11:00:00+09:00", "2030-01-07T11:30:00+09:00", instructor="大村 友恵"),
            event("2030-01-07T12:00:00+09:00", "2030-01-07T12:30:00+09:00"),
        ]
        with patch.object(google_calendar, "service", return_value=_Service(items)):
            periods = google_calendar.get_busy_periods(
                "shared", datetime(2030, 1, 7, 9, tzinfo=JST), datetime(2030, 1, 7, 13, tzinfo=JST), "大村 尊")
        self.assertEqual(len(periods), 2)
        self.assertEqual([period[0].hour for period in periods], [10, 12])

    def test_same_group_session_can_accept_more_participants(self):
        start = "2030-01-07T14:00:00+09:00"
        items = [event(start, "2030-01-07T14:30:00+09:00", instructor="スタジオ主催",
                       menu="サロン", kind="group")]
        with patch.object(google_calendar, "service", return_value=_Service(items)):
            periods = google_calendar.get_busy_periods(
                "shared", datetime(2030, 1, 7, 13, tzinfo=JST), datetime(2030, 1, 7, 15, tzinfo=JST),
                "スタジオ主催", {"menu": "サロン", "starts_at": start})
        self.assertEqual(periods, [])


if __name__ == "__main__":
    unittest.main()
