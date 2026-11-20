"""Authenticated customer selection and proxy reservations."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, time, timedelta
from functools import wraps

from flask import jsonify, request
from googleapiclient.errors import HttpError

import customer_directory as directory


def search_key(value):
    value = re.sub(r"\s", "", unicodedata.normalize("NFKC", str(value))).casefold()
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in value)


def register(app, core):
    def calendar_id():
        return core.calendar_for(core.GROUP_INSTRUCTOR)

    def protected(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not core.admin_required():
                return jsonify({"detail": "認証に失敗しました。ログインし直してください。"}), 401
            try:
                return fn(*args, **kwargs)
            except ValueError as error:
                return jsonify({"detail": str(error)}), 400
            except HttpError as error:
                if getattr(error.resp, "status", None) in {409, 412}:
                    return jsonify({"detail": "別の操作で更新されています。一覧を更新して選び直してください。"}), 409
                app.logger.exception("Staff reservation service unavailable")
                return jsonify({"detail": "情報を取得・保存できませんでした。入力を残して再度お試しください。"}), 503
            except Exception:
                app.logger.exception("Staff reservation service unavailable")
                return jsonify({"detail": "情報を取得・保存できませんでした。入力を残して再度お試しください。"}), 503
        return wrapped

    def json_body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError("入力内容を確認してください。")
        return data

    def validate_customer(data):
        if data.get("contact_confirmed") is not True:
            raise ValueError("ご本人のお名前・正しい電話番号と、代理予約に利用することを確認してください。")
        result = {}
        for key, limit in (("name", 80), ("kana", 80), ("note", 80)):
            value = str(data.get(key, "")).strip()
            if len(value) > limit or any(unicodedata.category(c).startswith("C") for c in value):
                raise ValueError("お名前・ふりがな・識別メモは各80文字以内、改行なしで入力してください。")
            result[key] = value
        phone = core.normalize_phone(data.get("phone", ""))
        if not result["name"] or not re.fullmatch(r"[0-9]{10,11}", phone):
            raise ValueError("お名前と10桁または11桁の電話番号を確認してください。")
        result.update(phone=phone, favorite=data.get("favorite") is True)
        return result

    @app.get("/api/admin/customers")
    @protected
    def staff_customers():
        query = search_key(request.args.get("q", ""))
        if len(query) > 80:
            raise ValueError("検索するお名前は80文字以内で入力してください。")
        rows = directory.list_customers(calendar_id())
        rows = [row for row in rows if not query or query in search_key(row["name"]) or query in search_key(row["kana"])]
        rows.sort(key=lambda row: (not row["favorite"], search_key(row["kana"] or row["name"]), row["id"]))
        return jsonify({"customers": rows})

    @app.get("/api/admin/customers/<customer_id>")
    @protected
    def staff_customer(customer_id):
        customer = directory.get_customer(calendar_id(), customer_id)
        if not customer:
            return jsonify({"detail": "登録されたお客様が見つかりません。"}), 404
        return jsonify(customer)

    @app.post("/api/admin/customers")
    @protected
    def staff_customer_create():
        data = json_body()
        customer_id = data.get("id")
        if not directory.valid_id(customer_id):
            raise ValueError("登録画面を開き直してください。")
        fields = validate_customer(data)
        existing = directory.get_customer(calendar_id(), customer_id)
        if existing:
            if all(existing[k] == fields[k] for k in fields):
                return jsonify(existing), 200
            return jsonify({"detail": "この登録は完了しています。一覧を更新してください。"}), 409
        return jsonify(directory.save_customer(calendar_id(), customer_id, fields)), 201

    @app.put("/api/admin/customers/<customer_id>")
    @protected
    def staff_customer_update(customer_id):
        data = json_body()
        fields = validate_customer(data)
        current = directory.get_customer(calendar_id(), customer_id)
        if not current:
            return jsonify({"detail": "登録されたお客様が見つかりません。"}), 404
        if data.get("revision") != current["revision"]:
            return jsonify({"detail": "お客様情報が更新されています。選び直して内容を確認してください。"}), 409
        return jsonify(directory.save_customer(calendar_id(), customer_id, fields, current["revision"]))

    def booking_candidate(event):
        if not event or core.event_private(event).get("source") != "ladanza-booking" or event.get("status") == "cancelled":
            return None
        row = core.daily_event_summary(event)
        return {"event_id": event["id"], "name": row["customer_name"],
                "phone": core.normalize_phone(core.description_value(event, "電話")),
                "starts_at": row["starts_at"], "status": row["status"],
                "menu": core.event_private(event).get("menu", ""), "instructor": row["instructor"],
                "customer_id": core.event_private(event).get("customer_id", "")}

    @app.get("/api/admin/customer-history")
    @protected
    def staff_customer_history():
        query = search_key(request.args.get("q", ""))
        if len(query) > 80:
            raise ValueError("検索するお名前は80文字以内で入力してください。")
        today = datetime.now(core.JST).date()
        start = datetime.combine(today - timedelta(days=365), time.min, core.JST)
        end = datetime.combine(today + timedelta(days=366), time.min, core.JST)
        events = core.list_calendar_events(calendar_id(), start, end)
        rows = [row for event in events if (row := booking_candidate(event)) and
                (not query or query in search_key(row["name"]))]
        rows.sort(key=lambda row: row["starts_at"], reverse=True)
        # Candidates are never automatically merged into customer identities.
        return jsonify({"candidates": rows[:50], "total": len(rows),
                        "from": start.date().isoformat(), "to": (end - timedelta(days=1)).date().isoformat()})

    @app.get("/api/admin/bookings/<event_id>/customer")
    @protected
    def staff_booking_customer(event_id):
        if not re.fullmatch(r"[a-v0-9]{5,1024}", event_id):
            raise ValueError("予約を選び直してください。")
        candidate = booking_candidate(core.get_event(calendar_id(), event_id))
        if not candidate:
            return jsonify({"detail": "予約が見つかりません。"}), 404
        return jsonify(candidate)

    @app.get("/api/admin/booking-slots")
    @protected
    def staff_booking_slots():
        return core.slots()

    @app.post("/api/admin/bookings")
    @protected
    def staff_booking_create():
        data = json_body()
        if data.get("booking_confirmed") is not True:
            raise ValueError("お客様からの予約依頼と予約内容を確認してください。")
        customer_id = data.get("customer_id")
        request_key = data.get("request_key", "")
        if not directory.valid_id(customer_id) or not isinstance(request_key, str) or not 20 <= len(request_key) <= 100:
            raise ValueError("お客様を選び直してください。")
        if any(not isinstance(data.get(key), str) for key in ("customer_revision", "menu", "instructor", "starts_at")):
            raise ValueError("お客様と予約内容を選び直してください。")
        intent = core.staff_intent(data)
        existing = core.get_event(calendar_id(), core.event_id_from_request(request_key))
        if existing:
            private = core.event_private(existing)
            if private.get("booking_source") == "staff" and private.get("staff_intent_hash") == intent:
                if not core.event_is_active(existing):
                    return jsonify({"detail": "この予約はキャンセル済みです。予約一覧をご確認ください。"}), 409
                token = core.reservation_token(existing["id"], request_key)
                return jsonify(core.booking_response(existing, core.os.getenv("PUBLIC_URL", request.url_root.rstrip("/")), token)), 200
            return jsonify({"detail": "この予約操作はすでに使用されています。予約一覧を確認してください。"}), 409
        customer = directory.get_customer(calendar_id(), customer_id)
        if not customer:
            return jsonify({"detail": "登録されたお客様が見つかりません。"}), 404
        if not data.get("customer_revision") or data["customer_revision"] != customer["revision"]:
            return jsonify({"detail": "お客様情報が更新されています。選び直して内容を確認してください。"}), 409
        target = core.parse_start(data["starts_at"]).date()
        today = datetime.now(core.JST).date()
        if not today <= target <= today + timedelta(days=365):
            raise ValueError("今日から1年以内の予約日を選んでください。")
        # Names and contacts come exclusively from the confirmed server-side record.
        payload = {key: data.get(key, "") for key in ("menu", "instructor", "starts_at", "request_key")}
        payload.update(name=customer["name"], phone=customer["phone"], privacy_consent=True)
        return core.create_booking(payload, customer=customer)
