from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _credentials_path() -> Path:
    configured = os.getenv("GOOGLE_CREDENTIALS_FILE")
    if configured:
        return Path(configured)
    render_secret = Path("/etc/secrets/credentials.json")
    return render_secret if render_secret.exists() else Path(__file__).with_name("credentials.json")


def service():
    path = _credentials_path()
    if not path.exists():
        raise RuntimeError(f"Google認証ファイルが見つかりません: {path}")
    credentials = Credentials.from_service_account_file(path, scopes=SCOPES)
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def calendar_for(instructor: str) -> str:
    mapping = json.loads(os.getenv("CALENDAR_MAP_JSON", "{}"))
    if instructor == "スタジオ主催":
        return os.environ["GROUP_CALENDAR_ID"]
    return mapping.get(instructor) or os.environ["GOOGLE_CALENDAR_ID"]


def get_busy_periods(calendar_id: str, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    response = service().freebusy().query(body={
        "timeMin": start.isoformat(), "timeMax": end.isoformat(), "timeZone": "Asia/Tokyo",
        "items": [{"id": calendar_id}],
    }).execute()
    periods = response["calendars"][calendar_id].get("busy", [])
    return [(datetime.fromisoformat(p["start"].replace("Z", "+00:00")),
             datetime.fromisoformat(p["end"].replace("Z", "+00:00"))) for p in periods]


def create_event(calendar_id: str, start: datetime, end: datetime, booking: dict) -> str:
    event = {"summary": f"La Danza｜{booking['menu']}｜{booking['name']}",
             "description": f"講師: {booking['instructor']}\nお名前: {booking['name']}\n電話: {booking['phone']}\nメール: {booking['email']}",
             "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Tokyo"},
             "end": {"dateTime": end.isoformat(), "timeZone": "Asia/Tokyo"}}
    return service().events().insert(calendarId=calendar_id, body=event).execute()["id"]


def delete_event(calendar_id: str, event_id: str | None):
    if event_id:
        service().events().delete(calendarId=calendar_id, eventId=event_id).execute()


def test_calendar_connection(calendar_id: str) -> dict:
    try:
        calendar = service().calendars().get(calendarId=calendar_id).execute()
        return {"connected": True, "calendar_id": calendar_id, "summary": calendar.get("summary", "La Danza予約用")}
    except Exception as exc:
        return {"connected": False, "calendar_id": calendar_id, "message": str(exc)[:160]}
