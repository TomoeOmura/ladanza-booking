from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

from google_calendar import (
    calendar_for,
    cancel_booking_event,
    create_event,
    find_booking_by_number,
    get_event,
    list_calendar_events,
    load_calendar_settings,
    save_calendar_settings,
    test_calendar_connection,
)

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")
app = Flask(__name__, static_folder=None)
JST = ZoneInfo("Asia/Tokyo")

DEFAULT_MENUS = {
    "個人レッスン 60分": {"duration": 60, "capacity": 1},
    "個人レッスン 30分": {"duration": 30, "capacity": 1},
    "無料体験 20分": {"duration": 20, "capacity": 1},
    "初心者パック 30分": {"duration": 30, "capacity": 1},
    "初級パック30分": {"duration": 30, "capacity": 1},
    "サロン・グループ": {"duration": 30, "capacity": 15},
}
TIME_SLOTS = [f"{hour:02d}:{minute:02d}" for hour in range(10, 22) for minute in (0, 30)]
DEFAULT_WEEKLY_SLOTS = {str(day): (TIME_SLOTS.copy() if day < 6 else []) for day in range(7)}
INSTRUCTORS = ["大村 尊", "大村 友恵", "廣瀬 裕貴", "スタジオ主催"]
DEFAULT_INSTRUCTOR_SLOTS = {name: deepcopy(DEFAULT_WEEKLY_SLOTS) for name in INSTRUCTORS}
DEFAULT_SETTINGS = {
    "open_time": "10:00", "close_time": "22:00", "slot_interval": 30,
    "closed_weekdays": [6], "weekly_slots": DEFAULT_WEEKLY_SLOTS,
    "instructor_slots": DEFAULT_INSTRUCTOR_SLOTS,
    "date_overrides": {name: {} for name in INSTRUCTORS}, "menus": DEFAULT_MENUS,
}

_settings_cache: dict | None = None
_settings_cache_until: datetime | None = None


def token_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def contact_digest(kind: str, value: str) -> str:
    return token_digest(f"{kind}:{value}")


def reservation_token(event_id: str, request_key: str) -> str:
    """Create a repeatable opaque token from the browser's random request key."""
    raw = event_id.encode("ascii") + b"." + request_key.encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def event_id_from_request(request_key: str) -> str:
    return "1d" + hashlib.sha256(request_key.encode("utf-8")).hexdigest()[:40]


def event_id_from_token(token: str) -> str | None:
    try:
        padding = "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(token + padding)
        event_id_bytes, request_key_bytes = raw.split(b".", 1)
        event_id = event_id_bytes.decode("ascii")
        request_key = request_key_bytes.decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    if not re.fullmatch(r"1d[0-9a-f]{40}", event_id):
        return None
    return event_id if 20 <= len(request_key) <= 100 else None


def parse_start(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JST)
    return parsed.astimezone(JST)


def event_private(event: dict) -> dict:
    return event.get("extendedProperties", {}).get("private", {})


def event_window(event: dict) -> tuple[datetime, datetime]:
    def parse(value: dict) -> datetime:
        if value.get("dateTime"):
            return parse_start(value["dateTime"])
        return datetime.combine(date.fromisoformat(value["date"]), time.min, JST)
    return parse(event["start"]), parse(event["end"])


def event_is_active(event: dict) -> bool:
    private = event_private(event)
    return (event.get("status") != "cancelled"
            and private.get("status", "confirmed") != "cancelled"
            and event.get("transparency") != "transparent")


def event_instructor(event: dict) -> str | None:
    tagged = event_private(event).get("instructor")
    if tagged:
        return tagged
    for line in (event.get("description") or "").splitlines():
        if line.startswith("講師:"):
            return line.split(":", 1)[1].strip() or None
    return None


def description_value(event: dict, label: str) -> str:
    prefix = f"{label}:"
    for line in (event.get("description") or "").splitlines():
        if line.startswith(prefix):
            return line.split(":", 1)[1].strip()
    return ""


def normalize_lookup_payload(payload: dict) -> tuple[str, str, str] | None:
    booking_id = str(payload.get("reservation_number", "")).strip().upper().replace(" ", "")
    if re.fullmatch(r"[0-9A-F]{8}", booking_id):
        booking_id = "LD-" + booking_id
    contact = str(payload.get("contact", "")).strip()
    if not re.fullmatch(r"LD-[0-9A-F]{8}", booking_id) or not contact:
        return None
    if "@" in contact:
        email = contact.lower()
        if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            return None
        return booking_id, "email", email
    phone = re.sub(r"[^0-9]", "", contact)
    if not re.fullmatch(r"\d{10,11}", phone):
        return None
    return booking_id, "phone", phone


def lookup_contact_matches(event: dict, kind: str, value: str) -> bool:
    private = event_private(event)
    stored_hash = private.get(f"{kind}_hash", "")
    if stored_hash:
        return hmac.compare_digest(stored_hash, contact_digest(kind, value))
    if kind == "email":
        stored = description_value(event, "メール").lower()
    else:
        stored = re.sub(r"[^0-9]", "", description_value(event, "電話"))
    return bool(stored) and hmac.compare_digest(stored, value)


def lookup_response(event: dict) -> dict:
    private = event_private(event)
    return {
        "reservation_number": private.get("booking_id") or reservation_number(event["id"]),
        "menu": private.get("menu", ""),
        "instructor": private.get("instructor", ""),
        "starts_at": private.get("starts_at") or event["start"].get("dateTime"),
        "duration_minutes": int(private.get("duration") or 0),
        "status": private.get("status", "confirmed"),
    }


def overlaps(start: datetime, end: datetime, other_start: datetime, other_end: datetime) -> bool:
    return start < other_end and end > other_start


def matching_group_event(event: dict, menu: str, instructor: str, start: datetime) -> bool:
    private = event_private(event)
    if private.get("source") != "ladanza-booking" or not event_is_active(event):
        return False
    event_start, _ = event_window(event)
    return (private.get("booking_kind") == "group" and private.get("menu") == menu
            and event_instructor(event) == instructor and event_start == start)


def event_blocks(event: dict, instructor: str, start: datetime, end: datetime,
                 group_menu: str | None = None) -> bool:
    if not event_is_active(event):
        return False
    other_start, other_end = event_window(event)
    if not overlaps(start, end, other_start, other_end):
        return False
    assigned = event_instructor(event)
    if assigned and assigned != instructor:
        return False
    if group_menu and matching_group_event(event, group_menu, instructor, start):
        return False
    return True


def normalize_settings(settings: dict) -> dict:
    settings = deepcopy(settings or DEFAULT_SETTINGS)
    if "weekly_slots" not in settings:
        closed = settings.get("closed_weekdays", [6])
        settings["weekly_slots"] = {str(day): (TIME_SLOTS.copy() if day not in closed else []) for day in range(7)}
    if "instructor_slots" not in settings:
        settings["instructor_slots"] = {name: deepcopy(settings["weekly_slots"]) for name in INSTRUCTORS}
    if "date_overrides" not in settings:
        settings["date_overrides"] = {name: {} for name in INSTRUCTORS}
    return settings


def get_settings(force: bool = False) -> dict:
    global _settings_cache, _settings_cache_until
    now = datetime.now(JST)
    if not force and _settings_cache is not None and _settings_cache_until and now < _settings_cache_until:
        return deepcopy(_settings_cache)
    stored = load_calendar_settings(calendar_for("スタジオ主催"))
    _settings_cache = normalize_settings(stored or DEFAULT_SETTINGS)
    _settings_cache_until = now + timedelta(seconds=60)
    return deepcopy(_settings_cache)


def set_settings(settings: dict) -> None:
    global _settings_cache, _settings_cache_until
    save_calendar_settings(calendar_for("スタジオ主催"), settings)
    _settings_cache = deepcopy(settings)
    _settings_cache_until = datetime.now(JST) + timedelta(seconds=60)


def clock(value: str) -> time:
    return time.fromisoformat(value)


def slot_window_enabled(start: datetime, duration: int, settings: dict, instructor: str) -> bool:
    cursor, end = start, start + timedelta(minutes=duration)
    while cursor < end:
        date_key = cursor.date().isoformat()
        overrides = settings["date_overrides"].get(instructor, {})
        if date_key in overrides:
            enabled = set(overrides[date_key])
        else:
            enabled = set(settings["instructor_slots"].get(instructor, {}).get(str(cursor.weekday()), []))
        if cursor.strftime("%H:%M") not in enabled:
            return False
        cursor += timedelta(minutes=30)
    return True


def valid_business_start(start: datetime, duration: int, settings: dict, instructor: str) -> bool:
    return start.second == 0 and start.minute in (0, 30) and slot_window_enabled(start, duration, settings, instructor)


def admin_required() -> bool:
    configured = os.getenv("ADMIN_TOKEN", "")
    supplied = request.headers.get("X-Admin-Token", "")
    return bool(configured) and secrets.compare_digest(supplied, configured)


def reservation_number(event_id: str) -> str:
    return "LD-" + event_id[-8:].upper()


def booking_response(event: dict, public_url: str, raw_token: str | None = None) -> dict:
    private = event_private(event)
    response = {"id": event["id"],
                "reservation_number": private.get("booking_id") or reservation_number(event["id"]),
                "starts_at": private.get("starts_at") or event["start"]["dateTime"]}
    if raw_token:
        response["reservation_url"] = f"{public_url}/reservation.html?token={raw_token}"
    return response


@app.get("/")
def index():
    return send_from_directory(BASE, "index.html")


@app.get("/reservation.html")
def reservation_file():
    return send_from_directory(BASE, "reservation.html")


@app.get("/reservation-lookup")
@app.get("/reservation-lookup.html")
def reservation_lookup_file():
    return send_from_directory(BASE, "reservation-lookup.html")


@app.get("/admin")
def admin_page():
    return send_from_directory(BASE, "admin.html")


@app.get("/privacy")
def privacy_page():
    return send_from_directory(BASE, "privacy.html")


@app.get("/health")
def health():
    return {"ok": True, "storage": "google-calendar"}


@app.get("/api/slots")
def slots():
    try:
        settings = get_settings()
    except Exception:
        app.logger.exception("Calendar-backed settings lookup failed")
        return jsonify({"detail": "予約設定を取得できません。時間をおいてお試しください"}), 503
    instructor = request.args.get("instructor") or "スタジオ主催"
    menu_name = request.args.get("menu") or ("サロン・グループ" if instructor == "スタジオ主催" else "個人レッスン 30分")
    menu = settings["menus"].get(menu_name)
    if not menu or instructor not in INSTRUCTORS:
        return jsonify({"detail": "メニューまたは講師が正しくありません"}), 400
    is_group = int(menu["capacity"]) > 1
    if (is_group and instructor != "スタジオ主催") or (not is_group and instructor == "スタジオ主催"):
        return jsonify({"detail": "メニューと講師の組み合わせが正しくありません"}), 400
    try:
        days = min(max(int(request.args.get("days", 14)), 1), 31)
    except ValueError:
        return jsonify({"detail": "表示日数が正しくありません"}), 400
    now = datetime.now(JST)
    opening, closing_time = time(10, 0), time(22, 0)
    range_start = datetime.combine(now.date(), opening, JST)
    range_end = datetime.combine(now.date() + timedelta(days=days), closing_time, JST)
    try:
        events = list_calendar_events(calendar_for(instructor), range_start, range_end)
    except Exception:
        app.logger.exception("Google Calendar availability lookup failed")
        return jsonify({"detail": "空き状況を取得できません。時間をおいてお試しください"}), 503
    result, slot_id = [], 1
    for offset in range(days):
        target = now.date() + timedelta(days=offset)
        closing = datetime.combine(target, closing_time, JST)
        overrides = settings["date_overrides"].get(instructor, {})
        labels = overrides[target.isoformat()] if target.isoformat() in overrides else settings["instructor_slots"].get(instructor, {}).get(str(target.weekday()), [])
        for label in labels:
            cursor = datetime.combine(target, clock(label), JST)
            end = cursor + timedelta(minutes=menu["duration"])
            if end > closing or not slot_window_enabled(cursor, menu["duration"], settings, instructor):
                continue
            matching = [event for event in events if is_group and matching_group_event(event, menu_name, instructor, cursor)]
            blocked = any(event_blocks(event, instructor, cursor, end, menu_name if is_group else None) for event in events)
            booked = len(matching) if is_group else (1 if blocked else 0)
            available = cursor > now and not blocked and booked < int(menu["capacity"])
            result.append({"id": slot_id, "instructor": instructor, "starts_at": cursor.isoformat(),
                           "duration_minutes": menu["duration"], "capacity": menu["capacity"],
                           "booked": booked, "remaining": max(int(menu["capacity"]) - booked, 0),
                           "available": available})
            slot_id += 1
    return jsonify(result)


@app.post("/api/bookings")
def book():
    try:
        settings = get_settings()
    except Exception:
        app.logger.exception("Calendar-backed settings lookup failed")
        return jsonify({"detail": "予約設定を取得できません。時間をおいてお試しください"}), 503
    payload = request.get_json(silent=True) or {}
    required = ("menu", "instructor", "starts_at", "name", "phone", "email", "request_key")
    if any(not str(payload.get(key, "")).strip() for key in required):
        return jsonify({"detail": "入力項目が不足しています"}), 400
    menu = settings["menus"].get(payload["menu"])
    instructor = str(payload["instructor"]).strip()
    if not menu or instructor not in INSTRUCTORS:
        return jsonify({"detail": "メニューまたは講師が正しくありません"}), 400
    is_group = int(menu["capacity"]) > 1
    if (is_group and instructor != "スタジオ主催") or (not is_group and instructor == "スタジオ主催"):
        return jsonify({"detail": "メニューと講師の組み合わせが正しくありません"}), 400
    if payload.get("privacy_consent") is not True:
        return jsonify({"detail": "個人情報の取り扱いへの同意が必要です"}), 400
    name = str(payload["name"]).strip()
    phone = re.sub(r"[^0-9]", "", str(payload["phone"]))
    email = str(payload["email"]).strip().lower()
    request_key = str(payload["request_key"]).strip()
    if (len(name) > 80 or not re.fullmatch(r"\d{10,11}", phone) or len(email) > 254
            or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)):
        return jsonify({"detail": "お名前、電話番号、メールアドレスを確認してください"}), 400
    if len(request_key) < 20 or len(request_key) > 100:
        return jsonify({"detail": "予約情報を再読み込みしてください"}), 400
    try:
        start = parse_start(payload["starts_at"])
    except (TypeError, ValueError):
        return jsonify({"detail": "日時が正しくありません"}), 400
    end = start + timedelta(minutes=menu["duration"])
    if (not valid_business_start(start, menu["duration"], settings, instructor)
            or end > datetime.combine(start.date(), clock(settings["close_time"]), JST)
            or start <= datetime.now(JST)):
        return jsonify({"detail": "営業時間外、定休日、または過去の日時です"}), 400
    source = str(payload.get("source", "website")).lower()
    source = source if source in {"website", "line"} else "website"
    public_url = os.getenv("PUBLIC_URL", request.url_root.rstrip("/"))
    request_hash = token_digest(request_key)
    event_id = event_id_from_request(request_key)
    calendar_id = calendar_for(instructor)
    try:
        token = reservation_token(event_id, request_key)
        existing = get_event(calendar_id, event_id)
        if existing and event_private(existing).get("request_key_hash") == request_hash:
            return jsonify(booking_response(existing, public_url, token)), 200
        events = list_calendar_events(calendar_id, start, end)
        matching = [event for event in events if is_group and matching_group_event(event, payload["menu"], instructor, start)]
        if any(event_blocks(event, instructor, start, end, payload["menu"] if is_group else None) for event in events):
            return jsonify({"detail": "この講師は同じ時間帯に別の予約があります"}), 409
        if is_group and len(matching) >= int(menu["capacity"]):
            return jsonify({"detail": "この回は満員です"}), 409
        consent_at = datetime.now(JST).isoformat()
        event_payload = {"menu": payload["menu"], "instructor": instructor, "name": name,
                         "phone": phone, "email": email, "capacity": menu["capacity"],
                         "duration": menu["duration"], "booking_id": reservation_number(event_id),
                         "request_key_hash": request_hash, "token_hash": token_digest(token),
                         "email_hash": contact_digest("email", email),
                         "phone_hash": contact_digest("phone", phone),
                         "privacy_consent_at": consent_at, "source": source}
        created = create_event(calendar_id, start, end, event_payload, event_id=event_id)
    except Exception:
        try:
            existing = get_event(calendar_id, event_id)
            if existing and event_private(existing).get("request_key_hash") == request_hash:
                return jsonify(booking_response(existing, public_url, token)), 200
        except Exception:
            pass
        app.logger.exception("Google Calendar booking registration failed")
        return jsonify({"detail": "予約を登録できませんでした。時間をおいてお試しください"}), 503
    app.logger.info("Booking confirmed event_id=%s instructor=%s start=%s", event_id, instructor, start.isoformat())
    return jsonify(booking_response(created, public_url, token)), 201


@app.get("/api/admin/settings")
def admin_settings_get():
    if not admin_required():
        return jsonify({"detail": "認証に失敗しました"}), 401
    try:
        return jsonify(get_settings(force=True))
    except Exception:
        app.logger.exception("Calendar-backed settings lookup failed")
        return jsonify({"detail": "設定を取得できませんでした"}), 503


@app.put("/api/admin/settings")
def admin_settings_put():
    if not admin_required():
        return jsonify({"detail": "認証に失敗しました"}), 401
    payload = request.get_json(silent=True) or {}
    try:
        instructor_slots = payload["instructor_slots"]
        date_overrides = payload.get("date_overrides", get_settings().get("date_overrides", {}))
        menus = payload["menus"]
        clean_instructors = {}
        for instructor in INSTRUCTORS:
            weekly = instructor_slots[instructor]
            clean_weekly = {}
            for day in range(7):
                values = weekly.get(str(day), [])
                if not isinstance(values, list) or any(value not in TIME_SLOTS for value in values):
                    raise ValueError
                clean_weekly[str(day)] = sorted(set(values))
            clean_instructors[instructor] = clean_weekly
        clean_overrides = {}
        for instructor in INSTRUCTORS:
            clean_dates = {}
            for date_key, values in date_overrides.get(instructor, {}).items():
                date.fromisoformat(date_key)
                if not isinstance(values, list) or any(value not in TIME_SLOTS for value in values):
                    raise ValueError
                clean_dates[date_key] = sorted(set(values))
            clean_overrides[instructor] = clean_dates
        clean_menus = {}
        for name in DEFAULT_MENUS:
            duration = int(menus[name]["duration"])
            capacity = int(menus[name].get("capacity", DEFAULT_MENUS[name]["capacity"]))
            if duration < 15 or duration > 180 or capacity < 1 or capacity > 15:
                raise ValueError
            clean_menus[name] = {"duration": duration, "capacity": capacity}
    except (KeyError, TypeError, ValueError):
        return jsonify({"detail": "設定値を確認してください"}), 400
    legacy_weekly = clean_instructors[INSTRUCTORS[0]]
    closed = [day for day in range(7) if not legacy_weekly[str(day)]]
    clean = {"open_time": "10:00", "close_time": "22:00", "slot_interval": 30,
             "closed_weekdays": closed, "weekly_slots": legacy_weekly,
             "instructor_slots": clean_instructors, "date_overrides": clean_overrides,
             "menus": clean_menus}
    try:
        set_settings(clean)
    except Exception:
        app.logger.exception("Calendar-backed settings update failed")
        return jsonify({"detail": "設定を保存できませんでした"}), 503
    return jsonify(clean)


@app.get("/api/admin/calendar-status")
def admin_calendar_status():
    if not admin_required():
        return jsonify({"detail": "認証に失敗しました"}), 401
    return jsonify({instructor: test_calendar_connection(calendar_for(instructor)) for instructor in INSTRUCTORS})


def reservation_event(token: str) -> dict | None:
    event_id = event_id_from_token(token)
    if not event_id:
        return None
    event = get_event(calendar_for("スタジオ主催"), event_id)
    if not event or event_private(event).get("source") != "ladanza-booking":
        return None
    stored_hash = event_private(event).get("token_hash", "")
    if not stored_hash or not hmac.compare_digest(stored_hash, token_digest(token)):
        return None
    return event


@app.get("/api/reservations/<token>")
def reservation(token):
    try:
        event = reservation_event(token)
    except Exception:
        app.logger.exception("Reservation lookup failed")
        return jsonify({"detail": "予約情報を取得できませんでした"}), 503
    if not event:
        return jsonify({"detail": "予約が見つかりません"}), 404
    private = event_private(event)
    return jsonify({"reservation_number": private.get("booking_id") or reservation_number(event["id"]),
                    "menu": private.get("menu", ""), "instructor": private.get("instructor", ""),
                    "starts_at": private.get("starts_at") or event["start"].get("dateTime"),
                    "duration_minutes": int(private.get("duration") or 0),
                    "status": private.get("status", "confirmed")})


def lookup_event(payload: dict) -> dict | None:
    normalized = normalize_lookup_payload(payload)
    if not normalized:
        return None
    booking_id, kind, value = normalized
    event = find_booking_by_number(calendar_for("スタジオ主催"), booking_id)
    if not event or not lookup_contact_matches(event, kind, value):
        return None
    return event


@app.post("/api/reservations/lookup")
def reservation_lookup():
    payload = request.get_json(silent=True) or {}
    try:
        event = lookup_event(payload)
    except Exception:
        app.logger.exception("Reservation number lookup failed")
        return jsonify({"detail": "予約情報を取得できませんでした"}), 503
    if not event:
        return jsonify({"detail": "予約番号または連絡先が一致しません"}), 404
    return jsonify(lookup_response(event))


@app.post("/api/reservations/lookup/cancel")
def reservation_lookup_cancel():
    payload = request.get_json(silent=True) or {}
    try:
        event = lookup_event(payload)
        if not event:
            return jsonify({"detail": "予約番号または連絡先が一致しません"}), 404
        if event_private(event).get("status", "confirmed") != "cancelled":
            cancel_booking_event(calendar_for("スタジオ主催"), event)
    except Exception:
        app.logger.exception("Google Calendar lookup cancellation failed")
        return jsonify({"detail": "キャンセル処理を完了できませんでした。La Danzaへお問い合わせください"}), 503
    return {"ok": True, "status": "cancelled"}


@app.post("/api/reservations/<token>/cancel")
def cancel(token):
    try:
        event = reservation_event(token)
        if not event:
            return jsonify({"detail": "予約が見つかりません"}), 404
        if event_private(event).get("status", "confirmed") != "cancelled":
            cancel_booking_event(calendar_for("スタジオ主催"), event)
    except Exception:
        app.logger.exception("Google Calendar cancellation failed")
        return jsonify({"detail": "キャンセル処理を完了できませんでした。La Danzaへお問い合わせください"}), 503
    return {"ok": True, "status": "cancelled"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=os.getenv("FLASK_DEBUG") == "1")
