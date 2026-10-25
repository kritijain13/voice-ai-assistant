
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Date, DateTime, ForeignKey, Integer, String, Text, Time,
    UniqueConstraint, CheckConstraint, Index
)

from database import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)

    user_message = Column(Text, nullable=False)

    ai_response = Column(Text, nullable=False)

    model = Column(String(100), nullable=False)

    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )


class Business(Base):
    __tablename__ = "businesses"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    business_type = Column(String(50), nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class Slot(Base):
    __tablename__ = "slots"
    __table_args__ = (
        UniqueConstraint("business_id", "slot_date", "start_time", name="uq_slot_business_date_time"),
        CheckConstraint("capacity > 0", name="ck_slot_capacity_positive"),
        CheckConstraint("booked_count >= 0 AND booked_count <= capacity", name="ck_slot_booked_count"),
        Index("ix_slots_date_time", "slot_date", "start_time"),
    )

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    slot_date = Column(Date, nullable=False)
    start_time = Column(Time, nullable=False)
    capacity = Column(Integer, nullable=False)
    booked_count = Column(Integer, nullable=False, default=0)


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("guests > 0", name="ck_booking_guests_positive"),
        Index("ix_bookings_business_status", "business_id", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    receipt_number = Column(String(32), nullable=True, unique=True)
    appointment_number = Column(String(32), nullable=True, unique=True)
    seat_numbers = Column(String(255), nullable=True)
    customer_name = Column(String(200), nullable=False)
    customer_phone = Column(String(30), nullable=True)
    business_id = Column(Integer, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False, index=True)
    slot_id = Column(Integer, ForeignKey("slots.id", ondelete="RESTRICT"), nullable=False, index=True)
    guests = Column(Integer, nullable=False)
    status = Column(String(30), nullable=False, default="confirmed", index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
