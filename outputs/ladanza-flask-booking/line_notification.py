from __future__ import annotations

import json
import os
from datetime import datetime
from urllib.request import Request, urlopen


LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
LINE_API_TIMEOUT_SECONDS = 5


def send_line_admin_notification(
    *,
    name: str,
    phone: str,
    menu: str,
    starts_at: datetime,
    instructor: str,
    reservation_number: str,
    participants: object | None = None,
) -> bool:
    """Push one new-booking notice, or skip it when LINE is not configured."""
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    admin_user_id = os.getenv("LINE_ADMIN_USER_ID", "").strip()
    if not token or not admin_user_id:
        return False

    lines = [
        "La Danzaに新規予約が入りました。",
        f"お客様名: {name}",
        f"電話番号: {phone}",
        f"メニュー: {menu}",
        f"予約日時: {starts_at.strftime('%Y年%m月%d日 %H:%M')}",
        f"担当講師: {instructor}",
    ]
    if participants not in (None, ""):
        lines.append(f"参加人数: {participants}名")
    lines.append(f"予約番号: {reservation_number}")

    body = json.dumps(
        {"to": admin_user_id, "messages": [{"type": "text", "text": "\n".join(lines)}]},
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        LINE_PUSH_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urlopen(request, timeout=LINE_API_TIMEOUT_SECONDS) as response:
        response.read()
    return True
