from __future__ import annotations

import logging
import os
import base64
import json
import zlib
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/calendar"]
logger = logging.getLogger(__name__)
JST = ZoneInfo("Asia/Tokyo")
SETTINGS_EVENT_ID = "1ada0a5e7710a5"
SETTINGS_START = "2037-01-01T00:00:00+09:00"
SETTINGS_END = "2037-01-01T00:01:00+09:00"


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
    """Return the single La Danza calendar used by every instructor."""
    del instructor
    return os.environ["GOOGLE_CALENDAR_ID"]


def _event_datetime(value: dict, *, end: bool = False) -> datetime:
    if value.get("dateTime"):
        parsed = datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00"))
        return parsed.astimezone(JST)
    parsed_date = date.fromisoformat(value["date"])
    return datetime.combine(parsed_date, time.min, JST)


def _legacy_instructor(event: dict) -> str | None:
    for line in (event.get("description") or "").splitlines():
        if line.startswith("講師:"):
            return line.split(":", 1)[1].strip() or None
    return None


def get_busy_periods(
    calendar_id: str,
    start: datetime,
    end: datetime,
    instructor: str,
    compatible_group: dict | None = None,
) -> list[tuple[datetime, datetime]]:
    """Return events that block one instructor on the shared calendar.

    Events created by this app are tagged with the instructor. Untagged events are
    treated as studio-wide closures. Existing app events are recognised from their
    legacy description. For a group menu, existing reservations for that exact
    session do not block additional participants; the caller enforces capacity
    by counting matching Google Calendar events.
    """
    periods: list[tuple[datetime, datetime]] = []
    page_token = None
    while True:
        response = service().events().list(
            calendarId=calendar_id,
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            showDeleted=False,
            orderBy="startTime",
            pageToken=page_token,
        ).execute()
        for event in response.get("items", []):
            if event.get("status") == "cancelled" or event.get("transparency") == "transparent":
                continue
            private = event.get("extendedProperties", {}).get("private", {})
            event_instructor = private.get("instructor") or _legacy_instructor(event)
            if event_instructor and event_instructor != instructor:
                continue
            if compatible_group and private.get("booking_kind") == "group":
                same_menu = private.get("menu") == compatible_group.get("menu")
                requested_start = compatible_group.get("starts_at")
                same_start = not requested_start or private.get("starts_at") == requested_start
                if same_menu and same_start:
                    continue
            periods.append((_event_datetime(event["start"]), _event_datetime(event["end"], end=True)))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return periods


def list_calendar_events(calendar_id: str, start: datetime, end: datetime) -> list[dict]:
    """Return every non-deleted event in a time range."""
    items: list[dict] = []
    page_token = None
    while True:
        response = service().events().list(
            calendarId=calendar_id,
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            showDeleted=False,
            orderBy="startTime",
            pageToken=page_token,
        ).execute()
        items.extend(response.get("items", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            return items


def get_event(calendar_id: str, event_id: str) -> dict | None:
    try:
        return service().events().get(calendarId=calendar_id, eventId=event_id).execute()
    except HttpError as exc:
        if getattr(exc.resp, "status", None) == 404:
            return None
        raise


def create_event(calendar_id: str, start: datetime, end: datetime, booking: dict, event_id: str | None = None) -> dict:
    private = {
        "source": "ladanza-booking",
        "instructor": booking["instructor"],
        "menu": booking["menu"],
        "starts_at": start.isoformat(),
        "booking_kind": "group" if int(booking.get("capacity", 1)) > 1 else "private",
        "booking_id": str(booking.get("booking_id", "")),
        "request_key_hash": str(booking.get("request_key_hash", "")),
        "token_hash": str(booking.get("token_hash", "")),
        "duration": str(booking.get("duration", "")),
        "capacity": str(booking.get("capacity", 1)),
        "privacy_consent_at": str(booking.get("privacy_consent_at", "")),
        "booking_source": str(booking.get("source", "website")),
        "status": "confirmed",
    }
    event = {"summary": f"La Danza｜{booking['menu']}｜{booking['name']}",
             "description": f"講師: {booking['instructor']}\nお名前: {booking['name']}\n電話: {booking['phone']}\nメール: {booking['email']}",
             "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Tokyo"},
             "end": {"dateTime": end.isoformat(), "timeZone": "Asia/Tokyo"},
             "extendedProperties": {"private": private}}
    if event_id:
        event["id"] = event_id
    logger.warning(
        "Google Calendar API registration started calendar_id=%s start=%s end=%s",
        calendar_id,
        start.isoformat(),
        end.isoformat(),
    )
    response_status: dict[str, int | None] = {"value": None}
    request = service().events().insert(calendarId=calendar_id, body=event)
    request.add_response_callback(
        lambda http_response: response_status.update(value=getattr(http_response, "status", None))
    )
    try:
        response = request.execute()
    except HttpError as exc:
        status = getattr(exc.resp, "status", None)
        logger.error(
            "Google Calendar API registration failed calendar_id=%s http_status=%s error=%s",
            calendar_id,
            status,
            exc.reason,
        )
        raise
    except Exception as exc:
        logger.error(
            "Google Calendar API registration failed calendar_id=%s http_status=%s error=%s",
            calendar_id,
            response_status["value"],
            str(exc),
        )
        raise

    created_event_id = response.get("id")
    logger.warning(
        "Google Calendar API registration succeeded calendar_id=%s http_status=%s event_id=%s html_link=%s",
        calendar_id,
        response_status["value"],
        created_event_id,
        response.get("htmlLink"),
    )
    if not created_event_id:
        raise RuntimeError("Google Calendar API response did not contain an event id")
    return response


def cancel_booking_event(calendar_id: str, event: dict) -> dict:
    private = dict(event.get("extendedProperties", {}).get("private", {}))
    private["status"] = "cancelled"
    private["cancelled_at"] = datetime.now(JST).isoformat()
    body = {
        "summary": "【キャンセル済み】" + (event.get("summary") or "La Danza予約"),
        "transparency": "transparent",
        "extendedProperties": {"private": private},
    }
    return service().events().patch(
        calendarId=calendar_id, eventId=event["id"], body=body
    ).execute()


def load_calendar_settings(calendar_id: str) -> dict | None:
    event = get_event(calendar_id, SETTINGS_EVENT_ID)
    if not event:
        return None
    private = event.get("extendedProperties", {}).get("private", {})
    try:
        count = int(private["parts"])
        encoded = "".join(private[f"data_{index:02d}"] for index in range(count))
        return json.loads(zlib.decompress(base64.urlsafe_b64decode(encoded)).decode("utf-8"))
    except (KeyError, TypeError, ValueError, zlib.error, json.JSONDecodeError):
        logger.exception("Calendar-backed settings could not be decoded")
        return None


def save_calendar_settings(calendar_id: str, settings: dict) -> dict:
    raw = json.dumps(settings, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii")
    chunks = [encoded[index:index + 900] for index in range(0, len(encoded), 900)]
    private = {"source": "ladanza-booking-settings", "parts": str(len(chunks))}
    private.update({f"data_{index:02d}": chunk for index, chunk in enumerate(chunks)})
    body = {
        "id": SETTINGS_EVENT_ID,
        "summary": "La Danza予約システム設定（削除しないでください）",
        "description": "予約受付時間などの設定を保存しています。削除・変更しないでください。",
        "start": {"dateTime": SETTINGS_START, "timeZone": "Asia/Tokyo"},
        "end": {"dateTime": SETTINGS_END, "timeZone": "Asia/Tokyo"},
        "transparency": "transparent",
        "extendedProperties": {"private": private},
    }
    existing = get_event(calendar_id, SETTINGS_EVENT_ID)
    if existing:
        return service().events().update(
            calendarId=calendar_id, eventId=SETTINGS_EVENT_ID, body=body
        ).execute()
    return service().events().insert(calendarId=calendar_id, body=body).execute()


def delete_event(calendar_id: str, event_id: str | None):
    if event_id:
        try:
            service().events().delete(calendarId=calendar_id, eventId=event_id).execute()
        except HttpError as exc:
            if getattr(exc.resp, "status", None) != 404:
                raise


def test_calendar_connection(calendar_id: str) -> dict:
    try:
        calendar = service().calendars().get(calendarId=calendar_id).execute()
        return {"connected": True, "calendar_id": calendar_id, "summary": calendar.get("summary", "La Danza予約用")}
    except Exception as exc:
        return {"connected": False, "calendar_id": calendar_id, "message": str(exc)[:160]}
