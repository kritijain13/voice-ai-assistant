
import logging
from datetime import date, datetime, timedelta
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from database import engine

logger = logging.getLogger("voice_ai")


def get_available_slots(business_type, slot_date, guests=1):
    query = text("""
        SELECT
            b.id AS business_id,
            b.name AS business_name,
            b.business_type,
            s.id AS slot_id,
            s.slot_date,
            s.start_time,
            s.capacity - s.booked_count AS available_capacity
        FROM businesses b
        JOIN slots s ON b.id = s.business_id
        WHERE (
              LOWER(b.business_type) = LOWER(:business_type)
              OR (:business_type IN ('hospital', 'clinic')
                  AND LOWER(b.business_type) IN ('hospital', 'clinic'))
        )
          AND s.slot_date = :slot_date
          AND s.capacity - s.booked_count >= :guests
          AND NOT EXISTS (
              SELECT 1 FROM bookings k
              WHERE k.slot_id = s.id AND k.status = 'confirmed'
          )
        ORDER BY s.start_time
    """)

    with engine.connect() as connection:
        rows = connection.execute(
            query,
            {
                "business_type": business_type,
                "slot_date": slot_date,
                "guests": guests
            }
        ).mappings().all()

    normalized = []
    for row in rows:
        slot = dict(row)
        if isinstance(slot["start_time"], timedelta):
            slot["start_time"] = (datetime.min + slot["start_time"]).time()
        normalized.append(slot)
    return normalized


def get_upcoming_slots(business_type, start_date, end_date, guests=1):
    query = text("""
        SELECT
            b.id AS business_id,
            b.name AS business_name,
            b.business_type,
            s.id AS slot_id,
            s.slot_date,
            s.start_time,
            s.capacity - s.booked_count AS available_capacity
        FROM businesses b
        JOIN slots s ON b.id = s.business_id
        WHERE (
              LOWER(b.business_type) = LOWER(:business_type)
              OR (:business_type IN ('hospital', 'clinic')
                  AND LOWER(b.business_type) IN ('hospital', 'clinic'))
        )
          AND s.slot_date BETWEEN :start_date AND :end_date
          AND s.capacity - s.booked_count >= :guests
          AND NOT EXISTS (
              SELECT 1 FROM bookings k
              WHERE k.slot_id = s.id AND k.status = 'confirmed'
          )
        ORDER BY s.slot_date, s.start_time
    """)

    with engine.connect() as connection:
        rows = connection.execute(
            query,
            {
                "business_type": business_type,
                "start_date": start_date,
                "end_date": end_date,
                "guests": guests
            }
        ).mappings().all()

    normalized = []
    for row in rows:
        slot = dict(row)
        if isinstance(slot["start_time"], timedelta):
            slot["start_time"] = (datetime.min + slot["start_time"]).time()
        normalized.append(slot)
    return normalized


def is_slot_booked(business_type, slot_date, start_time):
    query = text("""
        SELECT EXISTS (
            SELECT 1
            FROM slots s
            JOIN businesses b ON b.id = s.business_id
            JOIN bookings k ON k.slot_id = s.id
            WHERE (
                LOWER(b.business_type) = LOWER(:business_type)
                OR (:business_type IN ('hospital', 'clinic')
                    AND LOWER(b.business_type) IN ('hospital', 'clinic'))
            )
              AND s.slot_date = :slot_date
              AND s.start_time = :start_time
              AND k.status = 'confirmed'
        )
    """)
    with engine.connect() as connection:
        return bool(connection.execute(query, {
            "business_type": business_type,
            "slot_date": slot_date,
            "start_time": start_time
        }).scalar_one())


def create_booking(
    customer_name,
    business_id,
    slot_id,
    guests=1,
    customer_phone=None
):
    if guests < 1:
        return {"success": False, "message": "Guests must be at least 1."}

    try:
        with engine.begin() as connection:
            slot = connection.execute(
                text("""
                    SELECT s.id, s.business_id, s.slot_date,
                           s.start_time, s.capacity, s.booked_count,
                           b.name AS business_name, b.business_type
                    FROM slots s
                    JOIN businesses b ON b.id = s.business_id
                    WHERE s.id = :slot_id
                      AND s.business_id = :business_id
                    FOR UPDATE
                """),
                {
                    "slot_id": slot_id,
                    "business_id": business_id
                }
            ).mappings().first()

            if not slot:
                return {
                    "success": False,
                    "message": "Booking slot not found."
                }

            if slot["slot_date"] < date.today():
                return {
                    "success": False,
                    "message": "Cannot book a past date."
                }

            available = slot["capacity"] - slot["booked_count"]

            if available < guests:
                return {
                    "success": False,
                    "message": "Not enough availability for this slot."
                }

            existing = connection.execute(
                text("SELECT id FROM bookings WHERE slot_id = :slot_id AND status = 'confirmed' LIMIT 1"),
                {"slot_id": slot_id}
            ).first()
            if existing:
                return {
                    "success": False,
                    "message": "This cafe slot is already booked. Please choose another available time."
                }

            business_type = slot["business_type"].lower()
            seat_numbers = None
            appointment_number = None
            if business_type in ("cafe", "restaurant"):
                seat_start = int(slot["booked_count"]) + 1
                seat_numbers = ", ".join(
                    str(number) for number in range(seat_start, seat_start + guests)
                )

            insert_result = connection.execute(
                text("""
                    INSERT INTO bookings
                    (customer_name, customer_phone, business_id,
                     slot_id, guests, status, seat_numbers)
                    VALUES
                    (:customer_name, :customer_phone, :business_id,
                     :slot_id, :guests, 'confirmed', :seat_numbers)
                """),
                {
                    "customer_name": customer_name,
                    "customer_phone": customer_phone,
                    "business_id": business_id,
                    "slot_id": slot_id,
                    "guests": guests,
                    "seat_numbers": seat_numbers
                }
            )

            booking_id = insert_result.lastrowid
            receipt_number = f"BK-{booking_id:06d}"
            if business_type in ("hospital", "clinic", "salon", "gym"):
                appointment_number = f"APT-{booking_id:06d}"

            connection.execute(
                text("""UPDATE bookings
                       SET receipt_number = :receipt_number,
                           appointment_number = :appointment_number
                       WHERE id = :booking_id"""),
                {
                    "receipt_number": receipt_number,
                    "appointment_number": appointment_number,
                    "booking_id": booking_id
                }
            )

            connection.execute(
                text("""
                    UPDATE slots
                    SET booked_count = booked_count + :guests
                    WHERE id = :slot_id
                """),
                {
                    "guests": guests,
                    "slot_id": slot_id
                }
            )

            total_bookings = connection.execute(
                text("SELECT COUNT(*) FROM bookings WHERE status = 'confirmed'")
            ).scalar_one()

        return {
            "success": True,
            "booking_id": booking_id,
            "receipt": {
                "receipt_number": receipt_number,
                "appointment_number": appointment_number,
                "seat_numbers": seat_numbers,
                "business_name": slot["business_name"],
                "business_type": slot["business_type"],
                "customer_name": customer_name,
                "customer_phone": customer_phone,
                "slot_date": str(slot["slot_date"]),
                "start_time": str(slot["start_time"]),
                "guests": guests,
                "status": "confirmed"
            },
            "confirmed_booking_count": total_bookings,
            "message": (
                f"Booking confirm ho gayi. Receipt number {receipt_number}. "
                + (
                    f"Seat numbers {seat_numbers}."
                    if seat_numbers
                    else f"Appointment number {appointment_number}."
                    if appointment_number
                    else ""
                )
            )
        }

    except SQLAlchemyError:
        logger.exception("BOOKING_DATABASE_ERROR")
        return {
            "success": False,
            "message": "Database error. Booking was not completed."
        }
