import json
import os
import unittest
from copy import deepcopy
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

os.environ["GOOGLE_CALENDAR_ID"] = "shared-calendar@example.com"
os.environ["PUBLIC_URL"] = "https://booking.example.com"
os.environ["ADMIN_TOKEN"] = "test-admin-password-that-is-not-used-in-production"

import main  # noqa: E402
import line_notification  # noqa: E402

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
                 phone="070-3148-7791", source="website", participants=None):
        return {"menu": menu, "instructor": instructor, "starts_at": start or self._future_start(),
                "name": "予約 テスト", "phone": phone,
                "privacy_consent": True, "source": source, "participants": participants,
                "request_key": request_key or f"request-key-{os.urandom(16).hex()}"}

    def test_line_booking_sends_admin_notification(self):
        with patch.object(main, "send_line_admin_notification") as notify:
            response = self.client.post("/api/bookings", json=self._payload(source="line"))

        self.assertEqual(response.status_code, 201)
        notify.assert_called_once()

    def test_website_booking_does_not_send_admin_notification(self):
        with patch.object(main, "send_line_admin_notification") as notify:
            response = self.client.post("/api/bookings", json=self._payload(source="website"))

        self.assertEqual(response.status_code, 201)
        notify.assert_not_called()

    def test_booking_succeeds_without_line_environment(self):
        with patch.dict(os.environ, {
            "LINE_CHANNEL_ACCESS_TOKEN": "",
            "LINE_ADMIN_USER_ID": "",
        }), patch.object(line_notification, "urlopen") as urlopen:
            response = self.client.post("/api/bookings", json=self._payload(source="line"))

        self.assertEqual(response.status_code, 201)
        urlopen.assert_not_called()

    def test_booking_succeeds_when_line_api_fails(self):
        with patch.object(
            main, "send_line_admin_notification", side_effect=RuntimeError("LINE API failed")
        ):
            response = self.client.post("/api/bookings", json=self._payload(source="line"))

        self.assertEqual(response.status_code, 201)

    def test_line_notification_contains_booking_details(self):
        starts_at = datetime(2030, 1, 7, 14, 30, tzinfo=JST)
        token = os.urandom(24).hex()
        admin_user_id = os.urandom(16).hex()
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"{}"
        with patch.dict(os.environ, {
            "LINE_CHANNEL_ACCESS_TOKEN": token,
            "LINE_ADMIN_USER_ID": admin_user_id,
        }), patch.object(line_notification, "urlopen", return_value=response) as urlopen:
            sent = line_notification.send_line_admin_notification(
                name="予約 テスト",
                phone="07031487791",
                menu="サロン",
                starts_at=starts_at,
                instructor="スタジオ主催",
                participants=3,
                reservation_number="LD-1234ABCD",
            )

        self.assertTrue(sent)
        request = urlopen.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        message = body["messages"][0]["text"]
        for expected in (
            "新規予約", "予約 テスト", "07031487791", "サロン",
            "2030年01月07日 14:30", "スタジオ主催", "参加人数: 3名", "LD-1234ABCD",
        ):
            self.assertIn(expected, message)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], line_notification.LINE_API_TIMEOUT_SECONDS)

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
        schedule = self.client.get("/schedule")
        self.assertEqual(schedule.status_code, 200)
        schedule_page = schedule.get_data(as_text=True)
        self.assertIn("9月 ダンスサロン予定表", schedule_page)
        self.assertIn("19:00〜21:00", schedule_page)
        self.assertIn('9/7（月）</span><span class="time">13:00〜15:00', schedule_page)
        self.assertIn('9/28（月）</span><span class="time">13:00〜15:00', schedule_page)
        self.assertIn("一般 2,500円・会員 2,200円", schedule_page)
        schedule.close()
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
        self.assertIn("const publicMenus=['個人レッスン 60分','個人レッスン 30分','初心者パック 30分','初級パック 30分','サロン','チャーター 30分','テーマパークダンス 60分','社会人サークル 60分']", page)
        self.assertIn("const menus=trialOffer?[...publicMenus,'無料体験 20分']:publicMenus", page)
        self.assertIn("'テーマパークダンス 60分':'テーマパークダンス'", page)
        self.assertIn("60分・定員15名・残席表示", page)
        self.assertIn("${menuLabels[state.menu]||state.menu}", page)

        admin_response = self.client.get("/admin")
        admin = admin_response.get_data(as_text=True)
        admin_response.close()
        admin = self.client.get("/admin-settings.js").get_data(as_text=True)
        self.assertIn("instructors=['大村 尊','大村 友恵','廣瀬 裕貴','サロン','チャーター','テーマパークダンス','社会人サークル']", admin)
        self.assertNotIn("instructors=['大村 尊','大村 友恵','廣瀬 裕貴','スタジオ主催']", admin)
        self.assertIn("'テーマパークダンス 60分':'テーマパークダンス'", admin)

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
        start = self._future_charter_start()
        first_booking = None
        for index in range(6):
            response = self.client.post("/api/bookings", json=self._payload(
                instructor="スタジオ主催", menu="チャーター 30分", start=start,
                phone=f"070-0000-{index:04d}"))
            self.assertEqual(response.status_code, 201)
            if index == 0:
                first_booking = response.get_json()
        token = parse_qs(urlparse(first_booking["reservation_url"]).query)["token"][0]
        self.assertEqual(self.client.get(f"/api/reservations/{token}").get_json()["menu"], "チャーター")
        lookup = self.client.post("/api/reservations/lookup-all", json={"contact": "070-0000-0000"})
        self.assertEqual(lookup.get_json()["bookings"][0]["menu"], "チャーター")
        full = self.client.post("/api/bookings", json=self._payload(
            instructor="スタジオ主催", menu="チャーター 30分", start=start,
            phone="070-0000-9999"))
        self.assertEqual(full.status_code, 409)
        self.assertIn("満員", full.get_json()["detail"])
        wrong_instructor = self.client.post("/api/bookings", json=self._payload(
            instructor="大村 尊", menu="チャーター 30分", start=self._future_start(11)))
        self.assertEqual(wrong_instructor.status_code, 400)

    def test_salon_and_charter_use_separate_admin_schedules(self):
        start = datetime.fromisoformat(self._future_charter_start())
        day = str(start.weekday())
        self.saved_settings["instructor_slots"]["サロン"][day] = []
        self.saved_settings["instructor_slots"]["チャーター"][day] = ["10:00"]
        main._settings_cache = None

        salon = self.client.post("/api/bookings", json=self._payload(
            instructor="スタジオ主催", menu="サロン", start=start.isoformat()))
        charter = self.client.post("/api/bookings", json=self._payload(
            instructor="スタジオ主催", menu="チャーター 30分", start=start.isoformat()))

        self.assertEqual(salon.status_code, 400)
        self.assertEqual(charter.status_code, 201)

    def _future_charter_start(self):
        target = datetime.now(JST).date() + timedelta(days=1)
        while target.weekday() != 3 or target.day > 7:
            target += timedelta(days=1)
        return datetime(target.year, target.month, target.day, 10, tzinfo=JST).isoformat()

    def test_charter_only_first_thursday_even_with_date_overrides(self):
        settings = deepcopy(main.DEFAULT_SETTINGS)
        target = datetime(2026, 11, 1).date()
        stop = datetime(2029, 1, 1).date()
        while target < stop:
            labels = main.schedule_labels(target, settings, "チャーター")
            self.assertEqual(bool(labels), target.weekday() == 3 and target.day <= 7, target)
            target += timedelta(days=1)
        settings["date_overrides"]["チャーター"] = {
            "2026-11-05": [], "2026-11-12": ["20:00"], "2026-11-06": ["20:00"],
        }
        for day in (5, 6, 12):
            start = datetime(2026, 11, day, 20, tzinfo=JST)
            self.assertFalse(main.slot_window_enabled(start, 30, settings, "チャーター"))

    def test_charter_public_slots_and_direct_booking_apply_monthly_limit(self):
        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 10, 24, 10, tzinfo=JST)
        self.saved_settings["instructor_slots"]["チャーター"] = {str(d): ["20:00"] for d in range(7)}
        self.saved_settings["date_overrides"]["チャーター"]["2026-11-12"] = ["20:00"]
        with patch.object(main, "datetime", FixedDatetime):
            slots = self.client.get('/api/slots', query_string={"menu": "チャーター 30分"}).get_json()
            self.assertEqual([slot["starts_at"] for slot in slots], ["2026-11-05T20:00:00+09:00"])
            blocked = self.client.post('/api/bookings', json=self._payload(
                instructor="スタジオ主催", menu="チャーター 30分", start="2026-11-12T20:00:00+09:00"))
            self.assertEqual(blocked.status_code, 400)
            allowed = self.client.post('/api/bookings', json=self._payload(
                instructor="スタジオ主催", menu="チャーター 30分", start="2026-11-05T20:00:00+09:00"))
            self.assertEqual(allowed.status_code, 201)

    def test_daily_bookings_require_authentication_and_valid_date(self):
        with patch.object(main, "list_calendar_events") as events:
            for headers in ({}, {"X-Admin-Token": "wrong"}):
                response = self.client.get('/api/admin/bookings?date=2026-11-05', headers=headers)
                self.assertEqual(response.status_code, 401)
                self.assertIn('no-store', response.headers['Cache-Control'])
            for value in ('', '2026-02-30', '20261105', '2026-11-05T00:00:00', '9999-12-31'):
                response = self.client.get('/api/admin/bookings', query_string={"date": value},
                                           headers={"X-Admin-Token": os.environ['ADMIN_TOKEN']})
                self.assertEqual(response.status_code, 400)
            events.assert_not_called()

    def test_daily_bookings_show_day_overlap_cancellations_and_manual_events_without_secrets(self):
        def fixture(key, start, end, private=None, **extra):
            return {"id": key, "start": start, "end": end,
                    "extendedProperties": {"private": private or {}}, **extra}
        tags = {"source": "ladanza-booking", "menu": "個人レッスン 30分", "instructor": "廣瀬 裕貴",
                "booking_id": "LD-12345678", "token_hash": "never-return-this-token", "phone_hash": "never-return-this-hash"}
        items = [
            fixture('late', {"dateTime": "2026-11-05T18:00:00+09:00"}, {"dateTime": "2026-11-05T18:30:00+09:00"}, tags,
                    summary="La Danza｜個人レッスン 30分｜予約 太郎", description="お名前: 予約 太郎\n電話: 09012345678\nメール: private@example.test"),
            fixture('cancel', {"dateTime": "2026-11-05T19:00:00+09:00"}, {"dateTime": "2026-11-05T19:30:00+09:00"},
                    {**tags, "status": "cancelled"}, transparency="transparent"),
            fixture('night', {"dateTime": "2026-11-04T14:30:00Z"}, {"dateTime": "2026-11-04T16:00:00Z"}, summary="日をまたぐ予定"),
            fixture('all', {"date": "2026-11-05"}, {"date": "2026-11-06"}, summary="終日の予定"),
            fixture('before', {"dateTime": "2026-11-04T23:00:00+09:00"}, {"dateTime": "2026-11-05T00:00:00+09:00"}),
            fixture('after', {"dateTime": "2026-11-06T00:00:00+09:00"}, {"dateTime": "2026-11-06T01:00:00+09:00"}),
            fixture('settings', {"date": "2026-11-05"}, {"date": "2026-11-06"}, {"source": "ladanza-booking-settings"}),
            {"id": "deleted", "status": "cancelled"},
        ]
        with patch.object(main, 'list_calendar_events', return_value=items) as events:
            response = self.client.get('/api/admin/bookings?date=2026-11-05',
                                       headers={"X-Admin-Token": os.environ['ADMIN_TOKEN']})
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers['Cache-Control'])
        data = response.get_json()
        self.assertEqual(data['date'], '2026-11-05')
        self.assertEqual(data['timezone'], 'Asia/Tokyo')
        self.assertEqual(len(data['events']), 4)
        self.assertEqual(data['events'][0]['starts_at'], '2026-11-04T23:30:00+09:00')
        self.assertTrue(data['events'][1]['all_day'])
        self.assertEqual(data['events'][2]['customer_name'], '予約 太郎')
        self.assertEqual(data['events'][2]['resource'], '廣瀬 裕貴')
        self.assertEqual(data['events'][3]['status'], 'cancelled')
        for secret in ('never-return-this', '09012345678', 'private@example.test', 'extendedProperties'):
            self.assertNotIn(secret, response.get_data(as_text=True))
        self.assertEqual(events.call_args.args[1].isoformat(), '2026-11-05T00:00:00+09:00')
        self.assertEqual(events.call_args.args[2].isoformat(), '2026-11-06T00:00:00+09:00')

    def test_daily_bookings_failure_does_not_look_like_an_empty_day(self):
        with patch.object(main, 'list_calendar_events', side_effect=RuntimeError('private diagnostic')):
            response = self.client.get('/api/admin/bookings?date=2026-11-05',
                                       headers={"X-Admin-Token": os.environ['ADMIN_TOKEN']})
        self.assertEqual(response.status_code, 503)
        self.assertIn('取得できません', response.get_json()['detail'])
        self.assertNotIn('private diagnostic', response.get_data(as_text=True))

    def test_month_requires_auth_and_strict_month(self):
        self.assertEqual(self.client.get('/api/admin/month?month=2026-11').status_code, 401)
        headers = {"X-Admin-Token": os.environ['ADMIN_TOKEN']}
        for value in ('', '2026-13', '202611', '2026-1', '9999-12', '2026-11-01'):
            self.assertEqual(self.client.get('/api/admin/month', query_string={'month': value}, headers=headers).status_code, 400)

    def test_month_counts_overlap_and_hides_customer_data(self):
        tags = {'source': 'ladanza-booking', 'menu': '個人レッスン 30分', 'instructor': '廣瀬 裕貴'}
        self.events['booking'] = {'id': 'booking', 'summary': 'La Danza｜個人レッスン 30分｜非公開氏名',
            'start': {'dateTime': '2026-11-05T10:00:00+09:00'}, 'end': {'dateTime': '2026-11-05T10:30:00+09:00'},
            'extendedProperties': {'private': tags}}
        self.events['cancelled'] = {**deepcopy(self.events['booking']), 'id': 'cancelled',
            'extendedProperties': {'private': {**tags, 'status': 'cancelled'}}}
        self.events['overnight'] = {'id': 'overnight', 'summary': '非公開予定名',
            'start': {'dateTime': '2026-11-05T23:00:00+09:00'}, 'end': {'dateTime': '2026-11-06T01:00:00+09:00'}}
        response = self.client.get('/api/admin/month?month=2026-11', headers={'X-Admin-Token': os.environ['ADMIN_TOKEN']})
        self.assertEqual(response.status_code, 200)
        days = response.get_json()['days']
        self.assertEqual(len(days), 30)
        self.assertEqual(days[4]['counts']['廣瀬 裕貴'], {'bookings': 1, 'cancelled': 1, 'calendar': 0})
        self.assertEqual(days[4]['counts']['']['calendar'], 1)
        self.assertEqual(days[5]['counts']['']['calendar'], 1)
        self.assertEqual(days[11]['availability']['チャーター'], 'closed')
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertNotIn('非公開', response.get_data(as_text=True))

    def test_month_failure_is_not_empty_success(self):
        with patch.object(main, 'list_calendar_events', side_effect=RuntimeError('private diagnostic')):
            response = self.client.get('/api/admin/month?month=2026-11', headers={'X-Admin-Token': os.environ['ADMIN_TOKEN']})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private diagnostic', response.get_data(as_text=True))

    def test_monthly_rules_and_date_override_precedence(self):
        settings = main.normalize_settings(deepcopy(main.DEFAULT_SETTINGS))
        settings['recurrence_rules'] = {'廣瀬 裕貴': {'3': [1, 3, 5]}}
        for day, enabled in ((1, True), (8, False), (15, True), (22, False), (29, True)):
            result = main.schedule_labels(main.date(2026, 10, day), settings, '廣瀬 裕貴')
            self.assertEqual(bool(result), enabled)
        settings['date_overrides']['廣瀬 裕貴']['2026-10-08'] = ['17:00']
        self.assertEqual(main.schedule_labels(main.date(2026, 10, 8), settings, '廣瀬 裕貴'), ['17:00'])
        settings['date_overrides']['廣瀬 裕貴']['2026-10-15'] = []
        self.assertEqual(main.schedule_labels(main.date(2026, 10, 15), settings, '廣瀬 裕貴'), [])

    def test_stale_settings_are_rejected_and_old_clients_preserve_rules(self):
        headers = {'X-Admin-Token': os.environ['ADMIN_TOKEN']}
        first = self.client.get('/api/admin/settings', headers=headers).get_json()
        changed = deepcopy(first)
        changed['recurrence_rules'] = {'廣瀬 裕貴': {'3': [1, 3]}}
        self.assertEqual(self.client.put('/api/admin/settings', json=changed, headers=headers).status_code, 200)
        self.assertEqual(self.client.put('/api/admin/settings', json=first, headers=headers).status_code, 409)
        first.pop('revision');first.pop('recurrence_rules')
        self.assertEqual(self.client.put('/api/admin/settings', json=first, headers=headers).status_code, 200)
        self.assertEqual(self.saved_settings['recurrence_rules'], changed['recurrence_rules'])
        for invalid in ([0], [6], [], ['1'], [True]):
            first['recurrence_rules'] = {'廣瀬 裕貴': {'3': invalid}}
            self.assertEqual(self.client.put('/api/admin/settings', json=first, headers=headers).status_code, 400)

    def test_admin_free_times_match_conflicts_capacity_and_cancellations(self):
        target = main.date(2030, 1, 3)  # first Thursday
        now = datetime(2030, 1, 1, tzinfo=JST)
        settings = main.normalize_settings(deepcopy(main.DEFAULT_SETTINGS))
        settings['date_overrides']['チャーター'][target.isoformat()] = ['20:00']
        settings['date_overrides']['廣瀬 裕貴'][target.isoformat()] = ['10:00', '10:30', '17:00']
        event = {'id': 'one', 'start': {'dateTime': '2030-01-03T10:00:00+09:00'},
                 'end': {'dateTime': '2030-01-03T10:30:00+09:00'},
                 'extendedProperties': {'private': {'instructor': '廣瀬 裕貴'}}}
        result = {row['resource']: row for row in main.admin_availability(target, settings, [event], now)}
        self.assertEqual([s['start'] for s in result['廣瀬 裕貴']['slots']], ['10:30', '17:00'])
        self.assertEqual(result['チャーター']['slots'][0]['remaining'], 6)
        event['extendedProperties']['private']['status'] = 'cancelled'
        result = {row['resource']: row for row in main.admin_availability(target, settings, [event], now)}
        self.assertEqual(len(result['廣瀬 裕貴']['slots']), 3)
        group = {'id': 'group', 'start': {'dateTime': '2030-01-03T20:00:00+09:00'},
                 'end': {'dateTime': '2030-01-03T20:30:00+09:00'}, 'extendedProperties': {'private': {
                     'source': 'ladanza-booking', 'instructor': 'スタジオ主催', 'menu': 'チャーター 30分', 'booking_kind': 'group'}}}
        result = {row['resource']: row for row in main.admin_availability(target, settings, [group] * 5, now)}
        self.assertEqual(result['チャーター']['slots'][0]['remaining'], 1)
        result = {row['resource']: row for row in main.admin_availability(target, settings, [group] * 6, now)}
        self.assertEqual(result['チャーター']['slots'], [])
        manual = {'id': 'manual', 'start': {'date': target.isoformat()}, 'end': {'date': '2030-01-04'}}
        self.assertTrue(all(not row['slots'] for row in main.admin_availability(target, settings, [manual], now)))

    def test_circle_recurrence_and_existing_settings_migration(self):
        legacy = deepcopy(main.DEFAULT_SETTINGS)
        for key in ("instructor_slots", "date_overrides"):
            legacy[key].pop("社会人サークル")
        legacy["menus"].pop("社会人サークル 60分")
        legacy["date_overrides"][main.GROUP_INSTRUCTOR] = {"2026-10-14": ["10:00"]}
        migrated = main.normalize_settings(legacy)
        self.assertEqual(migrated["menus"]["社会人サークル 60分"], {"duration": 60, "capacity": 10})
        self.assertEqual(migrated["instructor_slots"]["サロン"], legacy["instructor_slots"]["サロン"])
        self.assertEqual(migrated["date_overrides"]["社会人サークル"], {})
        for day, expected in (("2026-10-14", ["19:30"]), ("2026-10-28", ["19:30"]),
                              ("2026-10-07", []), ("2026-10-21", []), ("2026-12-30", []),
                              ("2026-11-11", ["19:30"]), ("2026-11-25", ["19:30"]),
                              ("2027-01-13", ["19:30"]), ("2026-09-23", [])):
            self.assertEqual(main.schedule_labels(datetime.fromisoformat(day).date(), migrated, "社会人サークル"), expected)
        migrated["date_overrides"]["社会人サークル"]["2026-10-14"] = []
        migrated["date_overrides"]["社会人サークル"]["2026-10-21"] = ["18:00"]
        self.assertEqual(main.schedule_labels(datetime(2026, 10, 14).date(), migrated, "社会人サークル"), [])
        self.assertEqual(main.schedule_labels(datetime(2026, 10, 21).date(), migrated, "社会人サークル"), ["18:00"])

    def test_circle_slots_booking_capacity_and_cancellation(self):
        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 10, 1, 9, tzinfo=JST)
        with patch.object(main, "datetime", FixedDatetime):
            query = {"menu": "社会人サークル 60分", "instructor": "スタジオ主催", "days": 31}
            response = self.client.get("/api/slots", query_string=query)
            self.assertEqual(response.status_code, 200)
            self.assertEqual([x["starts_at"] for x in response.get_json()],
                             ["2026-10-14T19:30:00+09:00", "2026-10-28T19:30:00+09:00"])
            for start in ("2026-10-21T19:30:00+09:00", "2026-10-14T20:00:00+09:00"):
                bad = self.client.post("/api/bookings", json=self._payload(menu=query["menu"], instructor=query["instructor"], start=start))
                self.assertEqual(bad.status_code, 400)
            for index in range(10):
                created = self.client.post("/api/bookings", json=self._payload(
                    menu=query["menu"], instructor=query["instructor"], start="2026-10-14T19:30:00+09:00", phone=f"070-2000-{index:04d}"))
                self.assertEqual(created.status_code, 201)
            token = parse_qs(urlparse(created.get_json()["reservation_url"]).query)["token"][0]
            details = self.client.get(f"/api/reservations/{token}").get_json()
            self.assertEqual(details["menu"], "社会人サークル")
            self.assertEqual(details["duration_minutes"], 60)
            slots = self.client.get("/api/slots", query_string=query).get_json()
            self.assertEqual(slots[0]["remaining"], 0)
            self.assertFalse(slots[0]["available"])
            full = self.client.post("/api/bookings", json=self._payload(menu=query["menu"], instructor=query["instructor"], start="2026-10-14T19:30:00+09:00"))
            self.assertEqual(full.status_code, 409)
            self.assertEqual(self.client.post(f"/api/reservations/{token}/cancel").status_code, 200)
            self.assertEqual(self.client.get("/api/slots", query_string=query).get_json()[0]["remaining"], 1)

    def test_old_admin_page_preserves_circle_configuration(self):
        self.saved_settings["date_overrides"]["社会人サークル"] = {"2026-10-28": []}
        payload = deepcopy(self.saved_settings)
        payload["instructor_slots"].pop("社会人サークル")
        payload["date_overrides"].pop("社会人サークル")
        payload["menus"].pop("社会人サークル 60分")
        response = self.client.put("/api/admin/settings", json=payload,
                                   headers={"X-Admin-Token": os.environ["ADMIN_TOKEN"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["date_overrides"]["社会人サークル"], {"2026-10-28": []})
        self.assertEqual(response.get_json()["menus"]["社会人サークル 60分"]["capacity"], 10)

    def test_theme_park_dance_has_its_own_slot_and_capacity_fifteen(self):
        start = datetime.fromisoformat(self._future_start(16, 30))
        day = str(start.weekday())
        self.saved_settings["instructor_slots"]["テーマパークダンス"][day] = ["16:30", "17:30"]
        self.saved_settings["instructor_slots"]["サロン"][day] = []
        self.saved_settings["instructor_slots"]["チャーター"][day] = []
        main._settings_cache = None

        slots = self.client.get("/api/slots", query_string={
            "menu": "テーマパークダンス 60分", "instructor": "スタジオ主催",
            "days": (start.date() - datetime.now(JST).date()).days + 1,
        }).get_json()
        starts = {datetime.fromisoformat(item["starts_at"]).strftime("%H:%M") for item in slots}
        self.assertEqual(starts, {"16:30", "17:30"})

        unconfigured = self.client.post("/api/bookings", json=self._payload(
            instructor="スタジオ主催", menu="テーマパークダンス 60分",
            start=start.replace(hour=17, minute=0).isoformat()))
        self.assertEqual(unconfigured.status_code, 400)

        for index in range(15):
            response = self.client.post("/api/bookings", json=self._payload(
                instructor="スタジオ主催", menu="テーマパークダンス 60分",
                start=start.isoformat(), phone=f"070-1000-{index:04d}"))
            self.assertEqual(response.status_code, 201)

        full = self.client.post("/api/bookings", json=self._payload(
            instructor="スタジオ主催", menu="テーマパークダンス 60分",
            start=start.isoformat(), phone="070-1000-9999"))
        self.assertEqual(full.status_code, 409)
        self.assertIn("満員", full.get_json()["detail"])

        salon = self.client.post("/api/bookings", json=self._payload(
            instructor="スタジオ主催", menu="サロン", start=start.isoformat()))
        self.assertEqual(salon.status_code, 400)

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
        legacy["menus"].pop("テーマパークダンス 60分")
        legacy["instructor_slots"].pop("テーマパークダンス")
        legacy["date_overrides"].pop("テーマパークダンス")
        legacy["menus"]["初級パック30分"] = {"duration": 30, "capacity": 1}
        legacy["menus"]["サロン・グループ"] = {"duration": 30, "capacity": 15}
        migrated = main.normalize_settings(legacy)
        self.assertEqual(migrated["menus"]["初級パック 30分"]["duration"], 30)
        self.assertEqual(migrated["menus"]["サロン"]["capacity"], 10)
        self.assertEqual(migrated["menus"]["チャーター 30分"]["capacity"], 6)
        self.assertEqual(migrated["menus"]["テーマパークダンス 60分"], {"duration": 60, "capacity": 15})
        self.assertTrue(all(not slots for slots in migrated["instructor_slots"]["テーマパークダンス"].values()))
        self.assertNotIn("サロン・グループ", migrated["menus"])

    def test_legacy_studio_schedule_is_copied_to_salon_and_charter(self):
        legacy = deepcopy(main.DEFAULT_SETTINGS)
        weekly = deepcopy(main.DEFAULT_WEEKLY_SLOTS)
        weekly["0"] = ["18:00", "18:30"]
        legacy["instructor_slots"] = {
            "大村 尊": deepcopy(main.DEFAULT_WEEKLY_SLOTS),
            "大村 友恵": deepcopy(main.DEFAULT_WEEKLY_SLOTS),
            "廣瀬 裕貴": deepcopy(main.DEFAULT_WEEKLY_SLOTS),
            "スタジオ主催": weekly,
        }
        legacy["date_overrides"] = {
            "大村 尊": {}, "大村 友恵": {}, "廣瀬 裕貴": {},
            "スタジオ主催": {"2030-01-07": ["19:00"]},
        }

        migrated = main.normalize_settings(legacy)

        self.assertEqual(migrated["instructor_slots"]["サロン"]["0"], ["18:00", "18:30"])
        self.assertEqual(migrated["instructor_slots"]["チャーター"]["0"], ["18:00", "18:30"])
        self.assertEqual(migrated["date_overrides"]["サロン"]["2030-01-07"], ["19:00"])
        self.assertEqual(migrated["date_overrides"]["チャーター"]["2030-01-07"], ["19:00"])
        self.assertNotIn("スタジオ主催", migrated["instructor_slots"])

    def test_admin_has_no_default_password(self):
        self.assertEqual(self.client.get("/api/admin/settings").status_code, 401)
        page = self.client.get("/admin")
        self.assertNotIn("デモは admin", page.get_data(as_text=True))
        page.close()


if __name__ == "__main__":
    unittest.main()

