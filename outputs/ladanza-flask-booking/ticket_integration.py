"""Read-only daily totals for the separately authenticated ticket reception app."""
from __future__ import annotations

import base64
import re
import unicodedata
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from google.auth import crypt

JST = ZoneInfo("Asia/Tokyo")
INTEGRATION_PATH = "/api/integrations/ticket/today"


@lru_cache(maxsize=1)
def verifier():
    # Public verification key only. The private signing key lives in Sites secrets.
    return crypt.RSAVerifier.from_string(Path(__file__).with_name("ticket-integration-public.pem").read_bytes())


def authorized(headers, now: datetime) -> bool:
    timestamp = headers.get("X-LaDanza-Timestamp", "")
    signature = headers.get("X-LaDanza-Signature", "")
    if not re.fullmatch(r"[0-9]{10}", timestamp) or not re.fullmatch(r"[A-Za-z0-9_-]{342}", signature):
        return False
    age = int(now.timestamp()) - int(timestamp)
    if age < -10 or age > 60:
        return False
    try:
        message = f"GET\n{INTEGRATION_PATH}\n{timestamp}".encode()
        return bool(verifier().verify(message, base64.urlsafe_b64decode(signature + "==")))
    except Exception:
        return False


def normalize_name(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


def daily_totals(events: list[dict], now: datetime) -> dict:
    today = now.astimezone(JST).date()
    people, reservations, attendance, unidentified = set(), 0, 0, 0
    attendance_known = True
    seen_events = set()
    for event in events:
        private = event.get("extendedProperties", {}).get("private", {})
        if (private.get("source") != "ladanza-booking" or event.get("status") == "cancelled"
                or private.get("status") == "cancelled" or event.get("transparency") == "transparent"):
            continue
        raw_start = event.get("start", {}).get("dateTime")
        if not raw_start:
            # App bookings always have a timed start; malformed records must not appear as zero.
            raise ValueError("Reservation start is missing")
        start = datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
        if start.tzinfo is None:
            raise ValueError("Reservation timezone is missing")
        if start.astimezone(JST).date() != today:
            continue
        event_id = event.get("id")
        if event_id and event_id in seen_events:
            continue
        if event_id:
            seen_events.add(event_id)
        reservations += 1
        # Current booking form creates one event per participant, including group lessons.
        # Respect explicit legacy participant counts, but never infer group members' identities.
        raw_count = str(private.get("participants", "1"))
        count = int(raw_count) if re.fullmatch(r"[1-9][0-9]{0,3}", raw_count) else None
        if count is None:
            attendance_known = False
        else:
            attendance += count
        name = ""
        for line in (event.get("description") or "").splitlines():
            if line.startswith("お名前:"):
                name = normalize_name(line.split(":", 1)[1])
                break
        if not name:
            title = str(event.get("summary") or "").split("｜", 2)
            if len(title) == 3 and title[0] == "La Danza":
                name = normalize_name(title[2])
        phone_hash = private.get("phone_hash")
        if not name or not phone_hash or count != 1:
            unidentified += 1
        else:
            people.add((name, phone_hash))
    return {"date": today.isoformat(), "timezone": "Asia/Tokyo", "updated_at": now.isoformat(),
            "reservations": reservations, "attendance": attendance if attendance_known else None,
            "people": len(people) if unidentified == 0 else None, "unidentified_reservations": unidentified,
            "source": "ladanza-booking"}
