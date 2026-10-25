
import logging
from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException
from fastapi import Query
from pydantic import BaseModel, Field

from booking_service import (
    get_available_slots,
    get_upcoming_slots,
    is_slot_booked,
    create_booking
)

router = APIRouter(prefix="/api/booking", tags=["Booking"])
logger = logging.getLogger("voice_ai")


def format_time_12h(value):
    return value.strftime("%I:%M %p").lstrip("0")


# -------------------- REQUEST MODELS --------------------

class AvailabilityRequest(BaseModel):
    business_type: str
    slot_date: date
    guests: int = Field(default=1, ge=1)


class BookingRequest(BaseModel):
    customer_name: str
    business_id: int
    slot_id: int
    guests: int = Field(default=1, ge=1)
    customer_phone: str | None = None
    response_language: str = "auto"


class SuggestionRequest(BaseModel):
    business_type: str
    slot_date: date
    preferred_time: str
    guests: int = Field(default=1, ge=1)
    response_language: str = "auto"


# -------------------- CHECK AVAILABILITY --------------------

@router.post("/availability")
def check_availability(data: AvailabilityRequest):
    try:
        slots = get_available_slots(
            data.business_type,
            data.slot_date,
            data.guests
        )
        logger.info(
            "BOOKING_AVAILABILITY_CHECK | TYPE=%s | DATE=%s | GUESTS=%s | AVAILABLE_SLOTS=%s",
            data.business_type, data.slot_date, data.guests, len(slots)
        )

        return {
            "success": True,
            "available_slots": slots
        }

    except Exception:
        logging.exception("Booking availability failed")

        raise HTTPException(
            status_code=500,
            detail="Could not check availability."
        )


@router.get("/schedule")
def upcoming_schedule(
    business_type: str,
    start_date: date,
    days: int = Query(default=7, ge=1, le=31),
    guests: int = Query(default=1, ge=1)
):
    try:
        end_date = start_date + timedelta(days=days - 1)
        slots = get_upcoming_slots(business_type, start_date, end_date, guests)
        logger.info(
            "BOOKING_SCHEDULE_VIEW | TYPE=%s | START_DATE=%s | DAYS=%s | AVAILABLE_SLOTS=%s",
            business_type, start_date, days, len(slots)
        )
        return {"success": True, "available_slots": slots}
    except Exception:
        logger.exception("Booking schedule load failed")
        raise HTTPException(status_code=500, detail="Could not load upcoming slots.")


# -------------------- CREATE BOOKING --------------------

@router.post("/create")
def book_slot(data: BookingRequest):
    try:
        result = create_booking(
            customer_name=data.customer_name,
            business_id=data.business_id,
            slot_id=data.slot_id,
            guests=data.guests,
            customer_phone=data.customer_phone
        )

        if not result["success"]:
            logger.warning(
                "BOOKING_REJECTED | BUSINESS_ID=%s | SLOT_ID=%s | GUESTS=%s | REASON=%s",
                data.business_id, data.slot_id, data.guests, result["message"]
            )
            raise HTTPException(
                status_code=400,
                detail=result["message"]
            )

        if data.response_language == "hi":
            receipt = result["receipt"]
            message = f"बुकिंग पक्की हो गई। रसीद संख्या {receipt['receipt_number']}।"
            if receipt.get("seat_numbers"):
                message += f" आपकी सीट संख्या {receipt['seat_numbers']} है।"
            elif receipt.get("appointment_number"):
                message += f" आपका अपॉइंटमेंट नंबर {receipt['appointment_number']} है।"
            result["message"] = message

        logger.info(
            "BOOKING_CONFIRMED | RECEIPT=%s | APPOINTMENT=%s | SEATS=%s | NAME=%r | PHONE=%r | BUSINESS_ID=%s | SLOT_ID=%s | GUESTS=%s",
            result.get("receipt", {}).get("receipt_number"),
            result.get("receipt", {}).get("appointment_number"),
            result.get("receipt", {}).get("seat_numbers"),
            data.customer_name, data.customer_phone, data.business_id, data.slot_id, data.guests
        )
        return result

    except HTTPException:
        raise

    except Exception:
        logging.exception("Booking creation failed")

        raise HTTPException(
            status_code=500,
            detail="Could not create booking."
        )


# -------------------- SUGGEST ALTERNATIVE SLOTS --------------------

@router.post("/suggestions")
def suggest_alternative_slots(data: SuggestionRequest):
    try:
        # Preferred time validation
        try:
            requested_time = datetime.strptime(
                data.preferred_time,
                "%H:%M"
            ).time()
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="preferred_time must use HH:MM format, for example 19:00."
            )

        # Get slots that have enough capacity
        all_slots = get_available_slots(
            data.business_type,
            data.slot_date,
            data.guests
        )
        speak_hindi = data.response_language == "hi"

        # Normalize MySQL TIME values to Python time objects
        normalized_slots = []

        for slot in all_slots:
            slot = dict(slot)
            slot_time = slot["start_time"]

            if isinstance(slot_time, timedelta):
                slot_time = (
                    datetime.min + slot_time
                ).time()

            slot["start_time"] = slot_time
            normalized_slots.append(slot)

        booked_time = None
        if is_slot_booked(data.business_type, data.slot_date, requested_time):
            booked_time = requested_time
        elif 1 <= requested_time.hour <= 11:
            evening_time = requested_time.replace(hour=requested_time.hour + 12)
            if is_slot_booked(data.business_type, data.slot_date, evening_time):
                booked_time = evening_time

        # Check exact requested time
        exact_slot = [
            slot for slot in normalized_slots
            if slot["start_time"] == requested_time
        ]

        # If an unqualified 1-11 o'clock has no exact slot, try its evening
        # counterpart (for example 8 o'clock -> 20:00).
        matched_by_12_hour_clock = False
        if not exact_slot and 1 <= requested_time.hour <= 11:
            exact_slot = [
                slot for slot in normalized_slots
                if slot["start_time"].hour == requested_time.hour + 12
                and slot["start_time"].minute == requested_time.minute
            ]
            matched_by_12_hour_clock = bool(exact_slot)

        if exact_slot:
            other_slots = sorted(
                (slot for slot in normalized_slots if slot["slot_id"] != exact_slot[0]["slot_id"]),
                key=lambda slot: abs((datetime.combine(date.today(), slot["start_time"]) - datetime.combine(date.today(), requested_time)).total_seconds())
            )
            return {
                "available": True,
                "message": (
                    ("कैफ़े में " if speak_hindi else f"{data.business_type} mein ")
                    + (
                        (
                            f"आपके {requested_time.strftime('%I').lstrip('0')} बजे के लिए "
                            f"शाम {format_time_12h(exact_slot[0]['start_time'])} का स्लॉट उपलब्ध है।"
                            if speak_hindi
                            else f"Aapke {requested_time.strftime('%I').lstrip('0')} baje ke liye "
                            f"{format_time_12h(exact_slot[0]['start_time'])} ka slot available hai."
                        )
                        if matched_by_12_hour_clock
                        else (
                            f"{format_time_12h(requested_time)} का स्लॉट उपलब्ध है।"
                            if speak_hindi
                            else f"{format_time_12h(requested_time)} ka slot available hai."
                        )
                    )
                ),
                "selected_slot": exact_slot[0],
                "suggestions": other_slots
            }

        # Find up to 3 nearest available times
        alternatives = sorted(
            normalized_slots,
            key=lambda slot: abs(
                (
                    datetime.combine(date.today(), slot["start_time"])
                    - datetime.combine(date.today(), requested_time)
                ).total_seconds()
            )
        )

        if alternatives:
            times = [
                format_time_12h(slot["start_time"])
                for slot in alternatives
            ]

            if booked_time:
                message = (
                    f"{format_time_12h(booked_time)} का स्लॉट पहले से बुक है। "
                    f"उपलब्ध समय: {', '.join(times)}। इनमें से कोई समय चुनें।"
                    if speak_hindi else
                    f"{format_time_12h(booked_time)} ka slot pehle se booked hai. "
                    f"Available times: {', '.join(times)}. Inmein se koi time choose karein."
                )
            else:
                message = (
                    f"{format_time_12h(requested_time)} का स्लॉट उपलब्ध नहीं है। "
                    f"उपलब्ध समय: {', '.join(times)}। इनमें से कोई समय चुनें।"
                    if speak_hindi else
                    f"{format_time_12h(requested_time)} ka slot available nahi hai. "
                    f"Available times: {', '.join(times)}. Inmein se koi time choose karein."
                )

        else:
            if booked_time:
                message = (
                    f"{format_time_12h(booked_time)} का स्लॉट पहले से बुक है और इस तारीख़ पर कोई दूसरा समय उपलब्ध नहीं है। दूसरी तारीख़ चुनें।"
                    if speak_hindi else
                    f"{format_time_12h(booked_time)} ka slot pehle se booked hai, "
                    "aur is date par koi doosra open slot nahi hai. Doosri date try karein."
                )
            else:
                message = (
                    "इस तारीख़ पर कोई समय उपलब्ध नहीं है। कृपया दूसरी तारीख़ चुनें।"
                    if speak_hindi else
                    "Is date par koi suitable slot available nahi hai. "
                    "Aap doosri date try kar sakti hain."
                )

        return {
            "available": False,
            "message": message,
            "selected_slot": None,
            "suggestions": alternatives
        }

    except HTTPException:
        raise

    except Exception:
        logging.exception("Booking suggestions endpoint failed")

        raise HTTPException(
            status_code=500,
            detail="Could not check available slots."
        )
