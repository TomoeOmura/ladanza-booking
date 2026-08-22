from __future__ import annotations

import os
import json
import secrets
import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

from google_calendar import calendar_for, create_event, delete_event, get_busy_periods, test_calendar_connection

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")
app = Flask(__name__, static_folder=None)
JST = ZoneInfo("Asia/Tokyo")
DB_PATH = Path(os.getenv("DATABASE_PATH", BASE / "reservations.db"))

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
DEFAULT_INSTRUCTOR_SLOTS = {name: json.loads(json.dumps(DEFAULT_WEEKLY_SLOTS)) for name in INSTRUCTORS}
DEFAULT_SETTINGS = {"open_time": "10:00", "close_time": "22:00", "slot_interval": 30,
                    "closed_weekdays": [6], "weekly_slots": DEFAULT_WEEKLY_SLOTS,
                    "instructor_slots": DEFAULT_INSTRUCTOR_SLOTS,
                    "date_overrides": {name: {} for name in INSTRUCTORS}, "menus": DEFAULT_MENUS}


def db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT UNIQUE NOT NULL,
            menu TEXT NOT NULL, instructor TEXT NOT NULL, starts_at TEXT NOT NULL,
            duration INTEGER NOT NULL, name TEXT NOT NULL, phone TEXT NOT NULL,
            email TEXT NOT NULL, event_id TEXT, status TEXT NOT NULL DEFAULT 'confirmed',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        con.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('booking',?)",
                    (json.dumps(DEFAULT_SETTINGS, ensure_ascii=False),))


init_db()


def parse_start(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JST)
    return parsed.astimezone(JST)


def get_settings() -> dict:
    with db() as con:
        row = con.execute("SELECT value FROM settings WHERE key='booking'").fetchone()
    settings = json.loads(row["value"]) if row else json.loads(json.dumps(DEFAULT_SETTINGS))
    if "weekly_slots" not in settings:
        closed = settings.get("closed_weekdays", [6])
        settings["weekly_slots"] = {str(day): (TIME_SLOTS.copy() if day not in closed else []) for day in range(7)}
    if "instructor_slots" not in settings:
        settings["instructor_slots"] = {name: json.loads(json.dumps(settings["weekly_slots"])) for name in INSTRUCTORS}
    if "date_overrides" not in settings:
        settings["date_overrides"] = {name: {} for name in INSTRUCTORS}
    return settings


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
            schedule = settings["instructor_slots"].get(instructor, {})
            enabled = set(schedule.get(str(cursor.weekday()), []))
        if cursor.strftime("%H:%M") not in enabled:
            return False
        cursor += timedelta(minutes=30)
    return True


def valid_business_start(start: datetime, duration: int, settings: dict, instructor: str) -> bool:
    return start.second == 0 and start.minute in (0, 30) and slot_window_enabled(start, duration, settings, instructor)


def admin_required():
    return request.headers.get("X-Admin-Token") == os.getenv("ADMIN_TOKEN", "admin")


def overlaps(start: datetime, end: datetime, busy: list[tuple[datetime, datetime]]) -> bool:
    return any(start < busy_end and end > busy_start for busy_start, busy_end in busy)


@app.get("/")
def index():
    return send_from_directory(BASE, "index.html")


@app.get("/reservation.html")
def reservation_file():
    return send_from_directory(BASE, "reservation.html")


@app.get("/admin")
def admin_page():
    return send_from_directory(BASE, "admin.html")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/slots")
def slots():
    settings = get_settings()
    instructor = request.args.get("instructor") or "スタジオ主催"
    menu_name = request.args.get("menu") or ("サロン・グループ" if instructor == "スタジオ主催" else "個人レッスン 30分")
    menu = settings["menus"].get(menu_name)
    if not menu:
        return jsonify({"error": "無効なメニューです"}), 400
    days = min(max(int(request.args.get("days", 14)), 1), 31)
    now = datetime.now(JST)
    opening, closing_time = time(10, 0), time(22, 0)
    range_start = datetime.combine(now.date(), opening, JST)
    range_end = datetime.combine(now.date() + timedelta(days=days), closing_time, JST)
    busy = get_busy_periods(calendar_for(instructor), range_start, range_end)
    result, slot_id = [], 1
    for offset in range(days):
        target = now.date() + timedelta(days=offset)
        closing = datetime.combine(target, closing_time, JST)
        overrides = settings["date_overrides"].get(instructor, {})
        if target.isoformat() in overrides:
            labels = overrides[target.isoformat()]
        else:
            schedule = settings["instructor_slots"].get(instructor, {})
            labels = schedule.get(str(target.weekday()), [])
        for label in labels:
            cursor = datetime.combine(target, clock(label), JST)
            end = cursor + timedelta(minutes=menu["duration"])
            if end > closing or not slot_window_enabled(cursor, menu["duration"], settings, instructor):
                continue
            available = cursor > now and not overlaps(cursor, end, busy)
            result.append({"id": slot_id, "instructor": instructor, "starts_at": cursor.isoformat(),
                           "duration_minutes": menu["duration"], "capacity": menu["capacity"],
                           "booked": 0, "remaining": menu["capacity"], "available": available})
            slot_id += 1
    return jsonify(result)


@app.post("/api/bookings")
def book():
    settings = get_settings()
    payload = request.get_json(silent=True) or {}
    required = ("menu", "instructor", "starts_at", "name", "phone", "email")
    if any(not str(payload.get(key, "")).strip() for key in required):
        return jsonify({"detail": "入力項目が不足しています"}), 400
    menu = settings["menus"].get(payload["menu"])
    if not menu:
        return jsonify({"detail": "無効なメニューです"}), 400
    start = parse_start(payload["starts_at"])
    end = start + timedelta(minutes=menu["duration"])
    if not valid_business_start(start, menu["duration"], settings, payload["instructor"]) or end > datetime.combine(start.date(), clock(settings["close_time"]), JST) or start <= datetime.now(JST):
        return jsonify({"detail": "営業時間外、定休日、または過去の日時です"}), 400
    calendar_id = calendar_for(payload["instructor"])
    if overlaps(start, end, get_busy_periods(calendar_id, start, end)):
        return jsonify({"detail": "この時間帯はすでに予約されています"}), 409
    token = secrets.token_urlsafe(32)
    event_id = create_event(calendar_id, start, end, payload)
    app.logger.info(
        "Google Calendar event linked to booking calendar_id=%s event_id=%s instructor=%s start=%s",
        calendar_id,
        event_id,
        payload["instructor"],
        start.isoformat(),
    )
    with db() as con:
        cursor = con.execute("""INSERT INTO bookings
            (token,menu,instructor,starts_at,duration,name,phone,email,event_id)
            VALUES (?,?,?,?,?,?,?,?,?)""", (token, payload["menu"], payload["instructor"], start.isoformat(),
            menu["duration"], payload["name"], payload["phone"], payload["email"], event_id))
        booking_id = cursor.lastrowid
    public_url = os.getenv("PUBLIC_URL", request.url_root.rstrip("/"))
    return jsonify({"id": booking_id, "reservation_number": f"#{10000 + booking_id}",
                    "reservation_url": f"{public_url}/reservation.html?token={token}", "starts_at": start.isoformat()}), 201


@app.get("/api/admin/settings")
def admin_settings_get():
    if not admin_required():
        return jsonify({"detail": "認証に失敗しました"}), 401
    return jsonify(get_settings())


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
        for name in DEFAULT_MENUS:
            duration = int(menus[name]["duration"])
            capacity = int(menus[name].get("capacity", DEFAULT_MENUS[name]["capacity"]))
            if duration < 15 or duration > 180 or capacity < 1 or capacity > 15:
                raise ValueError
            menus[name] = {"duration": duration, "capacity": capacity}
    except (KeyError, TypeError, ValueError):
        return jsonify({"detail": "設定値を確認してください"}), 400
    legacy_weekly = clean_instructors[INSTRUCTORS[0]]
    closed = [day for day in range(7) if not legacy_weekly[str(day)]]
    clean = {"open_time": "10:00", "close_time": "22:00", "slot_interval": 30,
             "closed_weekdays": closed, "weekly_slots": legacy_weekly,
             "instructor_slots": clean_instructors, "date_overrides": clean_overrides, "menus": menus}
    with db() as con:
        con.execute("UPDATE settings SET value=? WHERE key='booking'", (json.dumps(clean, ensure_ascii=False),))
    return jsonify(clean)


@app.get("/api/admin/calendar-status")
def admin_calendar_status():
    if not admin_required():
        return jsonify({"detail": "認証に失敗しました"}), 401
    result = {}
    for instructor in INSTRUCTORS:
        result[instructor] = test_calendar_connection(calendar_for(instructor))
    return jsonify(result)


@app.get("/api/reservations/<token>")
def reservation(token):
    with db() as con:
        row = con.execute("SELECT * FROM bookings WHERE token=?", (token,)).fetchone()
    if not row:
        return jsonify({"detail": "予約が見つかりません"}), 404
    return jsonify({"reservation_number": f"#{10000 + row['id']}", "menu": row["menu"],
                    "instructor": row["instructor"], "starts_at": row["starts_at"],
                    "duration_minutes": row["duration"], "status": row["status"]})


@app.post("/api/reservations/<token>/cancel")
def cancel(token):
    with db() as con:
        row = con.execute("SELECT * FROM bookings WHERE token=?", (token,)).fetchone()
        if not row:
            return jsonify({"detail": "予約が見つかりません"}), 404
        if row["status"] != "cancelled":
            delete_event(calendar_for(row["instructor"]), row["event_id"])
            con.execute("UPDATE bookings SET status='cancelled' WHERE token=?", (token,))
    return {"ok": True, "status": "cancelled"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=os.getenv("FLASK_DEBUG") == "1")
