import os
import unittest
from copy import deepcopy
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import test_main as booking_tests
import customer_directory as directory
import main


class StaffBookingTests(unittest.TestCase):
    _save_settings = booking_tests.BookingApiTests._save_settings
    _list_events = booking_tests.BookingApiTests._list_events
    _find_booking = booking_tests.BookingApiTests._find_booking
    _find_bookings_by_phone_hash = booking_tests.BookingApiTests._find_bookings_by_phone_hash
    _cancel_event = booking_tests.BookingApiTests._cancel_event
    _update_contact = booking_tests.BookingApiTests._update_contact
    _future_start = booking_tests.BookingApiTests._future_start
    _payload = booking_tests.BookingApiTests._payload
    tearDown = booking_tests.BookingApiTests.tearDown

    def _create_event(self, calendar, start, end, booking, event_id=None):
        event = booking_tests.BookingApiTests._create_event(self, calendar, start, end, booking, event_id)
        event['description'] += '\nお名前: ' + booking['name']
        if booking.get('customer_id'):
            event['extendedProperties']['private'].update(customer_id=booking['customer_id'], staff_intent_hash=booking['staff_intent_hash'])
        self.events[event_id] = deepcopy(event)
        return event

    def setUp(self):
        booking_tests.BookingApiTests.setUp(self)
        self.customers = {}
        self.headers = {'X-Admin-Token': os.environ['ADMIN_TOKEN']}
        patch.object(directory, 'list_customers', side_effect=lambda _: deepcopy(list(self.customers.values()))).start()
        patch.object(directory, 'get_customer', side_effect=lambda _, key: deepcopy(self.customers.get(key))).start()
        patch.object(directory, 'save_customer', side_effect=self._save_customer).start()

    def _save_customer(self, _, key, fields, revision=None):
        row = {**fields, 'id': key, 'revision': (revision or '') + 'v', 'confirmed_at': '2026-11-20T12:00:00+09:00'}
        self.customers[key] = deepcopy(row)
        return row

    def register(self, **changes):
        data = {'id': 'c1' + os.urandom(16).hex(), 'name': '予約 テスト', 'phone': '０９０-１２３４-５６７８',
                'kana': 'よやく てすと', 'note': '', 'favorite': False, 'contact_confirmed': True, **changes}
        response = self.client.post('/api/admin/customers', headers=self.headers, json=data)
        self.assertEqual(response.status_code, 201)
        return response.get_json()

    def proxy(self, customer, **changes):
        return {'customer_id': customer['id'], 'customer_revision': customer['revision'],
                'menu': 'サロン', 'instructor': 'スタジオ主催', 'starts_at': self._future_start(13),
                'request_key': os.urandom(20).hex(), 'booking_confirmed': True, **changes}

    def post_booking(self, payload):
        return self.client.post('/api/admin/bookings', headers=self.headers, json=payload)

    def test_all_customer_operations_require_admin_and_are_not_cached(self):
        key = 'c1' + '1' * 32
        for method, path in [('get', '/api/admin/customers'), ('get', '/api/admin/customers/'+key),
                             ('post', '/api/admin/customers'), ('put', '/api/admin/customers/'+key),
                             ('get', '/api/admin/customer-history'), ('get', '/api/admin/bookings/abcde/customer'),
                             ('get', '/api/admin/booking-slots'), ('post', '/api/admin/bookings')]:
            response = getattr(self.client, method)(path)
            self.assertEqual(response.status_code, 401, path)
            self.assertIn('no-store', response.headers['Cache-Control'])
        directory.list_customers.assert_not_called()
        directory.save_customer.assert_not_called()

    def test_contact_confirmation_normalization_and_idempotent_registration(self):
        row = self.register()
        self.assertEqual(row['phone'], '09012345678')
        retry = self.client.post('/api/admin/customers', headers=self.headers, json={**row, 'contact_confirmed': True})
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(len(self.customers), 1)
        for changes in ({'contact_confirmed': False}, {'phone': '123'}, {'name': '名前\n電話: 123'}):
            bad = self.client.post('/api/admin/customers', headers=self.headers, json={**row, 'contact_confirmed': True, **changes})
            self.assertEqual(bad.status_code, 400)
        conflict = self.client.post('/api/admin/customers', headers=self.headers, json={**row, 'name': '別人', 'contact_confirmed': True})
        self.assertEqual(conflict.status_code, 409)

    def test_same_names_remain_separate_and_kana_search_favorites(self):
        first = self.register()
        second = self.register(phone='08012345678', favorite=True, note='別の方')
        response = self.client.get('/api/admin/customers?q=テスト', headers=self.headers)
        self.assertEqual([r['id'] for r in response.get_json()['customers']], [second['id'], first['id']])
        self.assertIn('no-store', response.headers['Cache-Control'])

    def test_directory_failure_is_an_error_not_an_empty_result(self):
        directory.list_customers.side_effect = RuntimeError('unavailable')
        response = self.client.get('/api/admin/customers', headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('customers', response.get_json())

    def test_customer_edits_require_latest_revision(self):
        row = self.register()
        url = '/api/admin/customers/' + row['id']
        payload = {**row, 'contact_confirmed': True, 'phone': '08012345678'}
        self.assertEqual(self.client.put(url, headers=self.headers, json={**payload, 'revision': 'old'}).status_code, 409)
        updated = self.client.put(url, headers=self.headers, json=payload)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(directory.save_customer.call_args.args[-1], row['revision'])
        self.assertEqual(self.events, {})

    def test_proxy_uses_verified_contact_and_customer_can_find_it(self):
        customer = self.register()
        response = self.post_booking(self.proxy(customer, name='偽名', phone='08000000000'))
        self.assertEqual(response.status_code, 201)
        row = response.get_json(); event = self.events[row['id']]
        self.assertIn('お名前: 予約 テスト', event['description'])
        self.assertIn('電話: 09012345678', event['description'])
        self.assertEqual(event['extendedProperties']['private']['booking_source'], 'staff')
        self.assertEqual(event['extendedProperties']['private']['customer_id'], customer['id'])
        found = self.client.post('/api/reservations/lookup-all', json={'contact': customer['phone']})
        self.assertEqual(found.status_code, 200)
        self.assertEqual(found.get_json()['bookings'][0]['reservation_number'], row['reservation_number'])
        token = parse_qs(urlparse(row['reservation_url']).query)['token'][0]
        self.assertEqual(self.client.get('/api/reservations/'+token).status_code, 200)
        self.assertEqual(self.client.post('/api/reservations/'+token+'/cancel').status_code, 200)

    def test_replay_after_lost_response_and_directory_edit_keeps_one_booking(self):
        customer = self.register(); payload = self.proxy(customer)
        created = self.post_booking(payload)
        self.customers[customer['id']]['revision'] = 'newer'
        self.customers[customer['id']]['phone'] = '08012345678'
        retry = self.post_booking(payload)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.get_json()['id'], created.get_json()['id'])
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.post_booking({**payload, 'starts_at': self._future_start(14)}).status_code, 409)

    def test_stale_contact_or_missing_confirmation_never_creates_booking(self):
        customer = self.register()
        self.assertEqual(self.post_booking(self.proxy(customer, customer_revision='stale')).status_code, 409)
        self.assertEqual(self.post_booking(self.proxy(customer, booking_confirmed=False)).status_code, 400)
        self.assertFalse(self.events)

    def test_cancelled_replay_does_not_claim_a_confirmed_booking(self):
        customer = self.register(); payload = self.proxy(customer)
        created = self.post_booking(payload).get_json()
        self.events[created['id']]['extendedProperties']['private']['status'] = 'cancelled'
        retry = self.post_booking(payload)
        self.assertEqual(retry.status_code, 409)
        self.assertIn('キャンセル済み', retry.get_json()['detail'])
        self.assertEqual(len(self.events), 1)

    def test_duplicate_customer_booking_is_blocked_even_after_phone_change(self):
        customer = self.register()
        first = self.post_booking(self.proxy(customer)); self.assertEqual(first.status_code, 201)
        self.customers[customer['id']]['phone'] = '08012345678'
        self.assertEqual(self.post_booking(self.proxy(self.customers[customer['id']])).status_code, 409)
        other = self.register(name='別のご家族')
        self.assertEqual(self.post_booking(self.proxy(other)).status_code, 201)

    def test_duplicate_existing_public_booking_is_blocked(self):
        customer = self.register()
        self.client.post('/api/bookings', json=self._payload(instructor='スタジオ主催', menu='サロン',
            start=self._future_start(13), phone=customer['phone']))
        duplicate = self.post_booking(self.proxy(customer))
        self.assertEqual(duplicate.status_code, 409)
        self.assertIn('予約済み', duplicate.get_json()['detail'])

    def test_capacity_schedule_and_free_trial_rules_are_preserved(self):
        self.saved_settings['menus']['サロン']['capacity'] = 2
        one = self.register(); two = self.register(name='二人目', phone='08011112222'); three = self.register(name='三人目', phone='08033334444')
        self.assertEqual(self.post_booking(self.proxy(one)).status_code, 201)
        self.assertEqual(self.post_booking(self.proxy(two)).status_code, 201)
        self.assertEqual(self.post_booking(self.proxy(three)).status_code, 409)
        self.assertEqual(self.post_booking(self.proxy(three, starts_at=self._future_start(3))).status_code, 400)
        trial = self.proxy(three, menu='無料体験 20分', instructor='大村 尊', starts_at=self._future_start(10))
        self.assertEqual(self.post_booking(trial).status_code, 201)
        self.assertEqual(self.post_booking({**trial, 'request_key': os.urandom(20).hex(), 'starts_at': self._future_start(11)}).status_code, 409)

    def test_public_request_cannot_claim_staff_or_customer_identity(self):
        response = self.client.post('/api/bookings', json={**self._payload(), 'source': 'staff', 'customer_id': 'c1'+'1'*32})
        self.assertEqual(response.status_code, 201)
        private = self.events[response.get_json()['id']]['extendedProperties']['private']
        self.assertEqual(private['booking_source'], 'website'); self.assertNotIn('customer_id', private)

    def test_history_does_not_automatically_register_or_merge_and_next_booking_reuses_identity(self):
        customer = self.register()
        booking = self.post_booking(self.proxy(customer)).get_json()
        response = self.client.get('/api/admin/customer-history?q=予約', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['candidates'][0]['customer_id'], customer['id'])
        candidate = self.client.get('/api/admin/bookings/'+booking['id']+'/customer', headers=self.headers).get_json()
        self.assertEqual(candidate['phone'], customer['phone'])
        self.assertEqual(candidate['menu'], 'サロン')
        self.assertEqual(len(self.customers), 1)

    def test_single_date_slots_and_directory_records_are_not_calendar_bookings(self):
        target = self._future_start(13)[:10]
        response = self.client.get('/api/admin/booking-slots', headers=self.headers,
            query_string={'date': target, 'menu': 'サロン', 'instructor': 'スタジオ主催'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(all(row['starts_at'].startswith(target) for row in response.get_json()))
        bad = self.client.get('/api/admin/booking-slots?date=2000-01-01', headers=self.headers)
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(main.overview_events([{'extendedProperties': {'private': {'source': directory.SOURCE}}}]), [])


class DirectoryStorageTests(unittest.TestCase):
    def test_durable_private_transparent_record_and_optimistic_edit(self):
        service = MagicMock(); operation = service.events.return_value.patch.return_value; operation.headers = {}
        fields = {'name': 'テスト', 'kana': '', 'phone': '09012345678', 'note': '', 'favorite': True}
        record = {'id': 'c1'+'1'*32, 'etag': 'v2', 'extendedProperties': {'private': {'source': directory.SOURCE, **fields}}}
        service.events.return_value.insert.return_value.execute.return_value = record
        operation.execute.return_value = record
        with patch.object(directory.calendar, 'service', return_value=service):
            directory.save_customer('calendar', record['id'], fields)
            body = service.events.return_value.insert.call_args.kwargs['body']
            self.assertEqual(body['visibility'], 'private'); self.assertEqual(body['transparency'], 'transparent')
            self.assertNotIn(fields['phone'], body['summary']); self.assertNotIn(fields['name'], body['summary'])
            directory.save_customer('calendar', record['id'], fields, 'v1')
            self.assertEqual(operation.headers['If-Match'], 'v1')
            self.assertNotIn('start', service.events.return_value.patch.call_args.kwargs['body'])

    def test_directory_pagination_and_foreign_event_rejection(self):
        service = MagicMock()
        record = {'id': 'c1'+'1'*32, 'etag': 'v1', 'extendedProperties': {'private': {'source': directory.SOURCE}}}
        service.events.return_value.list.return_value.execute.side_effect = [
            {'items': [record], 'nextPageToken': 'page2'}, {'items': [{'id':'foreign'}]}]
        with patch.object(directory.calendar, 'service', return_value=service):
            self.assertEqual(len(directory.list_customers('calendar')), 1)
        self.assertEqual(service.events.return_value.list.call_args.kwargs['pageToken'], 'page2')


if __name__ == '__main__':
    unittest.main()
