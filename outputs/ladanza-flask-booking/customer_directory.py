"""Staff-only customer records in the existing durable Calendar store."""
from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import google_calendar as calendar

SOURCE = "ladanza-booking-customer"
JST = ZoneInfo("Asia/Tokyo")


def valid_id(value):
    return isinstance(value, str) and re.fullmatch(r"c1[0-9a-f]{32}", value) is not None


def customer_from_event(event):
    if not event or event.get("status") == "cancelled":
        return None
    fields = event.get("extendedProperties", {}).get("private", {})
    if fields.get("source") != SOURCE:
        return None
    return {"id": event["id"], "revision": event["etag"],
            "name": fields.get("name", ""), "kana": fields.get("kana", ""),
            "phone": fields.get("phone", ""), "note": fields.get("note", ""),
            "favorite": fields.get("favorite") == "true",
            "confirmed_at": fields.get("confirmed_at", "")}


def get_customer(calendar_id, customer_id):
    if not valid_id(customer_id):
        return None
    return customer_from_event(calendar.get_event(calendar_id, customer_id))


def list_customers(calendar_id):
    result, page = [], None
    api = calendar.service().events()
    while True:
        data = api.list(calendarId=calendar_id, privateExtendedProperty=f"source={SOURCE}",
                        showDeleted=False, maxResults=2500, pageToken=page).execute()
        result.extend(row for item in data.get("items", [])
                      if (row := customer_from_event(item)) is not None)
        page = data.get("nextPageToken")
        if not page:
            return result


def save_customer(calendar_id, customer_id, fields, revision=None):
    private = {"source": SOURCE, **{key: fields[key] for key in ("name", "kana", "phone", "note")},
               "favorite": "true" if fields["favorite"] else "false",
               "confirmed_at": datetime.now(JST).isoformat()}
    body = {"summary": "La Danza顧客台帳（削除しないでください）",
            "description": "代理予約用のお客様情報です。変更はLa Danza予約管理から行ってください。",
            "transparency": "transparent", "visibility": "private",
            "extendedProperties": {"private": private}}
    api = calendar.service().events()
    if revision:
        operation = api.patch(calendarId=calendar_id, eventId=customer_id, body=body)
        operation.headers["If-Match"] = revision
    else:
        body.update(id=customer_id, start={"dateTime": "2037-01-02T00:00:00+09:00"},
                    end={"dateTime": "2037-01-02T00:01:00+09:00"})
        operation = api.insert(calendarId=calendar_id, body=body)
    return customer_from_event(operation.execute())
