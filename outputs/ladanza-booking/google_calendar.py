from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path

from google.oauth2.credentials import Credentials
from google.oauth2.service_account import Credentials as ServiceCredentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _service():
    service_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    token_json = os.getenv("GOOGLE_TOKEN_JSON")
    if service_file and Path(service_file).exists():
        credentials = ServiceCredentials.from_service_account_file(service_file, scopes=SCOPES)
    elif token_json:
        credentials = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
    else:
        return None
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def calendar_id_for(instructor: str) -> str:
    key = "GOOGLE_CALENDAR_" + instructor.upper().replace(" ", "_")
    return os.getenv(key, os.getenv("GOOGLE_CALENDAR_ID", "primary"))


def create_booking_event(slot, booking, participant_count: int, lesson_duration_minutes: int) -> str | None:
    service = _service()
    if not service:
        return None
    event = {
        "summary": f"La Danza｜{booking.menu}｜{booking.name}",
        "description": f"参加者: {booking.name}\n電話: {booking.phone}\n定員利用: {participant_count}/{slot.capacity}",
        "start": {"dateTime": slot.starts_at.isoformat(), "timeZone": "Asia/Tokyo"},
        "end": {"dateTime": (slot.starts_at + timedelta(minutes=lesson_duration_minutes)).isoformat(), "timeZone": "Asia/Tokyo"},
    }
    result = service.events().insert(calendarId=calendar_id_for(slot.instructor), body=event).execute()
    return result.get("id")


def delete_booking_event(instructor: str, event_id: str | None) -> None:
    service = _service()
    if service and event_id:
        service.events().delete(calendarId=calendar_id_for(instructor), eventId=event_id).execute()
