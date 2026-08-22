from __future__ import annotations

import os
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from dotenv import load_dotenv

from database import Booking, SessionLocal, Slot, init_db
from google_calendar import create_booking_event, delete_booking_event

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")
app = FastAPI(title="La Danza Reservation", docs_url="/api/docs")
MENUS = {
    "個人レッスン 60分": {"capacity": 1, "duration": 60},
    "個人レッスン 30分": {"capacity": 1, "duration": 30},
    "無料体験 20分": {"capacity": 1, "duration": 20},
    "初心者パック 30分": {"capacity": 1, "duration": 30},
    "初級パック30分": {"capacity": 1, "duration": 30},
    "サロン・グループ": {"capacity": 15, "duration": 30},
}


def db_session():
    with SessionLocal() as db:
        yield db


def admin_guard(x_admin_token: str = Header(default="")):
    if x_admin_token != os.getenv("ADMIN_TOKEN", "ladanza-demo"):
        raise HTTPException(401, "管理者トークンが正しくありません")


class BookingIn(BaseModel):
    slot_id: int
    menu: str
    name: str = Field(min_length=1, max_length=100)
    phone: str = Field(min_length=8, max_length=40)
    email: EmailStr


class SlotIn(BaseModel):
    instructor: str
    starts_at: datetime
    duration_minutes: int = Field(default=60, ge=30, le=180)
    capacity: int = Field(default=1, ge=1, le=15)


class SlotPatch(BaseModel):
    is_open: bool | None = None
    capacity: int | None = Field(default=None, ge=1, le=15)


def occupancy(db: Session, slot_id: int) -> int:
    return db.scalar(select(func.count()).select_from(Booking).where(Booking.slot_id == slot_id, Booking.status == "confirmed")) or 0


def slot_json(db: Session, slot: Slot) -> dict:
    booked = occupancy(db, slot.id)
    closed = not slot.is_open or slot.starts_at <= datetime.now() + timedelta(hours=2) or booked >= slot.capacity
    return {"id": slot.id, "instructor": slot.instructor, "starts_at": slot.starts_at.isoformat(), "duration_minutes": slot.duration_minutes,
            "capacity": slot.capacity, "booked": booked, "remaining": max(slot.capacity - booked, 0), "available": not closed}


@app.on_event("startup")
def startup():
    init_db()


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(BASE / "index.html")


@app.get("/modern", include_in_schema=False)
def modern():
    return FileResponse(BASE / "index-modern.html")


@app.get("/premium-dark", include_in_schema=False)
def premium_dark():
    return FileResponse(BASE / "index-premium-dark.html")


@app.get("/admin", include_in_schema=False)
def admin():
    return FileResponse(BASE / "admin.html")


@app.get("/api/slots")
def list_slots(instructor: str | None = None, db: Session = Depends(db_session)):
    q = select(Slot).where(Slot.starts_at >= datetime.now()).order_by(Slot.starts_at)
    if instructor:
        q = q.where(Slot.instructor == instructor)
    return [slot_json(db, s) for s in db.scalars(q).all()]


@app.post("/api/bookings", status_code=201)
def create_booking(data: BookingIn, db: Session = Depends(db_session)):
    if data.menu not in MENUS:
        raise HTTPException(400, "無効なメニューです")
    slot = db.get(Slot, data.slot_id)
    if not slot:
        raise HTTPException(404, "予約枠がありません")
    current = occupancy(db, slot.id)
    menu = MENUS[data.menu]
    capacity = min(slot.capacity, menu["capacity"]) if data.menu != "サロン・グループ" else slot.capacity
    if not slot.is_open or slot.starts_at <= datetime.now() + timedelta(hours=2) or current >= capacity:
        raise HTTPException(409, "この枠は受付を終了しました")
    if data.menu != "サロン・グループ":
        requested_end = slot.starts_at + timedelta(minutes=menu["duration"])
        existing = db.scalars(select(Booking).join(Slot).where(
            Booking.status == "confirmed", Slot.instructor == slot.instructor
        )).all()
        for reserved in existing:
            reserved_end = reserved.slot.starts_at + timedelta(minutes=MENUS.get(reserved.menu, {"duration": reserved.slot.duration_minutes})["duration"])
            if slot.starts_at < reserved_end and requested_end > reserved.slot.starts_at:
                raise HTTPException(409, "選択した時間帯は既に予約されています")
    booking = Booking(slot_id=slot.id, menu=data.menu, name=data.name, phone=data.phone, email=str(data.email))
    db.add(booking); db.flush()
    event_id = create_booking_event(slot, booking, current + 1, menu["duration"])
    if event_id:
        booking.calendar_event_id = event_id
    db.commit(); db.refresh(booking)
    reservation_url = f"{os.getenv('PUBLIC_URL', 'http://localhost:8000')}/reservation/{booking.cancel_token}"
    send_confirmation(booking.email, booking.name, slot.starts_at, reservation_url)
    return {"id": booking.id, "reservation_number": f"#{10000 + booking.id}", "reservation_url": reservation_url,
            "starts_at": slot.starts_at.isoformat()}


@app.get("/reservation/{token}", include_in_schema=False)
def reservation_page(token: str):
    return FileResponse(BASE / "reservation.html")


@app.get("/cancel/{token}", include_in_schema=False)
def legacy_cancel_link(token: str):
    return RedirectResponse(f"/reservation/{token}", status_code=302)


@app.get("/api/reservations/{token}")
def reservation_details(token: str, db: Session = Depends(db_session)):
    booking = db.scalar(select(Booking).where(Booking.cancel_token == token))
    if not booking:
        raise HTTPException(404, "予約が見つかりません")
    return {"reservation_number": f"#{10000 + booking.id}", "name": booking.name, "menu": booking.menu,
            "instructor": booking.slot.instructor, "starts_at": booking.slot.starts_at.isoformat(),
            "duration_minutes": MENUS.get(booking.menu, {"duration": booking.slot.duration_minutes})["duration"],
            "status": booking.status}


@app.post("/api/reservations/{token}/cancel")
def cancel_reservation(token: str, db: Session = Depends(db_session)):
    booking = db.scalar(select(Booking).where(Booking.cancel_token == token))
    if not booking:
        raise HTTPException(404, "予約が見つかりません")
    if booking.status == "cancelled":
        return {"ok": True, "status": "cancelled"}
    booking.status = "cancelled"
    delete_booking_event(booking.slot.instructor, booking.calendar_event_id)
    db.commit()
    return {"ok": True, "status": "cancelled"}


@app.get("/api/admin/overview", dependencies=[Depends(admin_guard)])
def overview(db: Session = Depends(db_session)):
    now, week = datetime.now(), datetime.now() + timedelta(days=7)
    bookings = db.scalars(select(Booking).join(Slot).where(Booking.status == "confirmed", Slot.starts_at.between(now, week)).order_by(Slot.starts_at)).all()
    return {"bookings": [{"id": b.id, "name": b.name, "menu": b.menu, "phone": b.phone, "starts_at": b.slot.starts_at.isoformat(), "instructor": b.slot.instructor} for b in bookings],
            "slots": [slot_json(db, s) for s in db.scalars(select(Slot).where(Slot.starts_at.between(now, week)).order_by(Slot.starts_at)).all()]}


@app.post("/api/admin/slots", dependencies=[Depends(admin_guard)], status_code=201)
def add_slot(data: SlotIn, db: Session = Depends(db_session)):
    slot = Slot(**data.model_dump()); db.add(slot); db.commit(); db.refresh(slot)
    return slot_json(db, slot)


@app.patch("/api/admin/slots/{slot_id}", dependencies=[Depends(admin_guard)])
def patch_slot(slot_id: int, data: SlotPatch, db: Session = Depends(db_session)):
    slot = db.get(Slot, slot_id)
    if not slot: raise HTTPException(404, "枠がありません")
    for key, value in data.model_dump(exclude_none=True).items(): setattr(slot, key, value)
    db.commit(); return slot_json(db, slot)


@app.delete("/api/admin/bookings/{booking_id}", dependencies=[Depends(admin_guard)])
def admin_cancel(booking_id: int, db: Session = Depends(db_session)):
    booking = db.get(Booking, booking_id)
    if not booking: raise HTTPException(404, "予約がありません")
    booking.status = "cancelled"; delete_booking_event(booking.slot.instructor, booking.calendar_event_id); db.commit()
    return {"ok": True}


@app.post("/api/admin/bookings", dependencies=[Depends(admin_guard)], status_code=201)
def admin_add_booking(data: BookingIn, db: Session = Depends(db_session)):
    return create_booking(data, db)


def send_confirmation(to: str, name: str, starts_at: datetime, reservation_url: str) -> None:
    host = os.getenv("SMTP_HOST")
    if not host: return
    msg = EmailMessage(); msg["Subject"] = "La Danza ご予約完了"; msg["From"] = os.getenv("SMTP_FROM", "reservation@ladanza.jp"); msg["To"] = to
    msg.set_content(f"{name} 様\n\nご予約ありがとうございます。\n日時: {starts_at:%Y年%m月%d日 %H:%M}\n予約内容の確認・キャンセル: {reservation_url}")
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587"))) as smtp:
        smtp.starttls(); smtp.login(os.getenv("SMTP_USER", ""), os.getenv("SMTP_PASSWORD", "")); smtp.send_message(msg)
