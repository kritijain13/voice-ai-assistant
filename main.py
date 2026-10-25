
import logging
import time
import os
import requests
from pathlib import Path
from booking_routes import router as booking_router
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from groq import Groq
from dotenv import load_dotenv

from database import Base, engine, get_db
from models import Conversation, Business, Slot, Booking


# --------------------------------------------------
# 1. ENVIRONMENT VARIABLES
# --------------------------------------------------

load_dotenv()

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

# Reuse HTTP connections for ElevenLabs
elevenlabs_session = requests.Session()

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None


# --------------------------------------------------
# 2. LOGGING SETUP
# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOG_DIR / "latency.log"

logger = logging.getLogger("voice_ai")
logger.setLevel(logging.INFO)
logger.propagate = False

if not logger.handlers:
    file_handler = logging.StreamHandler(
        LOG_FILE,
        encoding="utf-8"
    )

    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)s | %(message)s"
        )
    )

    logger.addHandler(file_handler)


# --------------------------------------------------
# 3. FASTAPI SETUP
# --------------------------------------------------

app = FastAPI(title="Voice AI Assistant")
app.include_router(booking_router)

Base.metadata.create_all(bind=engine)

STATIC_DIR = BASE_DIR / "static"

app.mount(
    "/static",
    StaticFiles(directory=STATIC_DIR),
    name="static"
)

MAX_AUDIO_BYTES = 20 * 1024 * 1024


# --------------------------------------------------
# 4. HOME PAGE
# --------------------------------------------------

@app.get("/")
def home():
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-store, max-age=0"}
    )


# --------------------------------------------------
# 5. HEALTH CHECK
# --------------------------------------------------

@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "message": "Voice AI Assistant backend is running"
    }


# --------------------------------------------------
# 6. BROWSER LATENCY LOGGING
# --------------------------------------------------

@app.post("/api/latency")
def log_browser_latency(data: dict):
    question = str(data.get("question", ""))[:500]
    voice_latency = data.get("time_to_voice_start_ms")

    logger.info(
        "BROWSER_LATENCY | QUESTION=%r | "
        "TIME_TO_VOICE_START_MS=%s",
        question,
        voice_latency
    )

    return {"logged": True}


# --------------------------------------------------
# 6A. ELEVENLABS REALTIME STT LATENCY
# --------------------------------------------------

@app.post("/api/stt-latency")
def log_stt_latency(data: dict):
    question = str(data.get("question", "")).strip()[:500]

    first_partial_ms = data.get("stt_first_partial_latency_ms")
    final_transcript_ms = data.get("stt_final_latency_ms")

    # STT latency is logged separately.
    # It is NOT added to TOTAL_BACKEND_LATENCY_MS.

    logger.info(
        "REALTIME_STT_LATENCY | "
        "QUESTION=%r | "
        "STT_FIRST_PARTIAL_LATENCY_MS=%s | "
        "STT_LATENCY_MS=%s",
        question,
        first_partial_ms,
        final_transcript_ms
    )

    return {"logged": True}


# --------------------------------------------------
# 6B. ELEVENLABS REALTIME TOKEN ENDPOINT
# --------------------------------------------------

@app.get("/api/scribe-token")
def get_scribe_token():

    if not ELEVENLABS_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="ELEVENLABS_API_KEY .env file mein set nahi hai."
        )

    try:
        response = elevenlabs_session.post(
            "https://api.elevenlabs.io/v1/single-use-token/realtime_scribe",
            headers={
                "xi-api-key": ELEVENLABS_API_KEY
            },
            timeout=10
        )

        response.raise_for_status()
        token = response.json().get("token")

        if not token:
            raise HTTPException(
                status_code=502,
                detail="ElevenLabs realtime token nahi mila."
            )

        return {"token": token}

    except requests.RequestException as exc:
        logger.exception("SCRIBE_TOKEN_FAILED")

        raise HTTPException(
            status_code=502,
            detail="Realtime transcription token request fail hui."
        ) from exc


# --------------------------------------------------
# 6C. ANSWER ENDPOINT FOR REALTIME TRANSCRIPT
# --------------------------------------------------

@app.post("/api/answer")
def generate_answer(
    data: dict,
    db: Session = Depends(get_db)
):
    total_start = time.perf_counter()
    response_language = str(data.get("response_language", "auto")).lower()

    if not GROQ_API_KEY or not groq_client:
        raise HTTPException(
            status_code=500,
            detail=(
                "अभी मैं जवाब तैयार नहीं कर पा रही। कृपया थोड़ी देर बाद फिर पूछें।"
                if response_language == "hi"
                else "I can't prepare an answer right now. Please try again shortly."
            )
        )

    transcript = str(data.get("transcript", "")).strip()

    if not transcript:
        prompt_again = (
            "मैं आपकी बात साफ़ नहीं सुन पाई। कृपया दोबारा बोलें।"
            if response_language == "hi"
            else "I didn't hear that clearly. Could you please say it again?"
        )
        raise HTTPException(status_code=422, detail=prompt_again)

    # STEP 1: GROQ LLM
    groq_start = time.perf_counter()

    try:
        completion = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a fast, helpful voice assistant. "
                        + (
                            "The user is speaking Hindi. Reply only in natural Hindi written in Devanagari. "
                            "If the transcript is unclear or garbled, ask a short clarification in Hindi. "
                            if response_language == "hi"
                            else "Reply in the user's language. If the transcript is unclear, ask a short clarification in that language. "
                        )
                        + "Answer naturally in 1-2 short sentences. Avoid unnecessary details and Markdown. "
                        + "If you do not know, say so plainly and do not invent facts. "
                        + "Never mention internal tools, model names, empty responses, or technical errors. "
                        + "Never switch to an unrelated language or copy garbled transcription."
                    )
                },
                {
                    "role": "user",
                    "content": transcript
                }
            ],
            temperature=0.4,
            max_completion_tokens=80,
            reasoning_effort="low"
        )

        answer = (
            completion.choices[0].message.content or ""
        ).strip()

    except Exception as exc:
        logger.exception("GROQ_REQUEST_FAILED_REALTIME")
        answer = (
            "अभी मैं जवाब तैयार नहीं कर पाई। कृपया थोड़ी देर बाद फिर पूछें।"
            if response_language == "hi"
            else "I couldn't prepare an answer just now. Please try again shortly."
        )

    groq_latency = round(
        (time.perf_counter() - groq_start) * 1000, 2
    )

    if not answer:
        answer = (
            "मैं आपकी बात ठीक से समझ नहीं पाई। कृपया एक बार फिर बोलें।"
            if response_language == "hi"
            else "I didn't understand that clearly. Could you please say it again?"
        )

    # STEP 2: SQL DATABASE
    db_start = time.perf_counter()

    try:
        conversation = Conversation(
            user_message=transcript,
            ai_response=answer,
            model=GROQ_MODEL
        )

        db.add(conversation)
        db.commit()
        db.refresh(conversation)

    except Exception as exc:
        db.rollback()
        logger.exception("DATABASE_SAVE_FAILED_REALTIME")

        raise HTTPException(
            status_code=500,
            detail="Conversation SQL database mein save nahi hui."
        ) from exc

    db_latency = round(
        (time.perf_counter() - db_start) * 1000, 2
    )

    # STEP 3: TOTAL BACKEND LATENCY
    # Realtime STT is excluded because it happens separately
    # in the browser through ElevenLabs Realtime Scribe.
    backend_latency = round(
        (time.perf_counter() - total_start) * 1000, 2
    )

    logger.info(
        "REALTIME_ANSWER_SUCCESS | "
        "QUESTION=%r | ANSWER=%r | MODEL=%s | "
        "TOOLS=Groq LLM, SQL Database, Browser SpeechSynthesis | "
        "GROQ_LATENCY_MS=%.2f | "
        "DB_LATENCY_MS=%.2f | "
        "TOTAL_BACKEND_LATENCY_MS=%.2f | "
        "DATABASE_SAVED=True",
        transcript,
        answer,
        GROQ_MODEL,
        groq_latency,
        db_latency,
        backend_latency
    )

    return {
        "id": conversation.id,
        "transcript": transcript,
        "answer": answer,
        "model": GROQ_MODEL,
        "saved_to_database": True
    }


# --------------------------------------------------
# 7. EXISTING VOICE API
# --------------------------------------------------

@app.post("/api/voice")
def voice_to_text_and_answer(
    audio: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    total_start = time.perf_counter()

    # Check API keys
    if not ELEVENLABS_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="ELEVENLABS_API_KEY .env file mein set nahi hai."
        )

    if not GROQ_API_KEY or not groq_client:
        raise HTTPException(
            status_code=500,
            detail="GROQ_API_KEY .env file mein set nahi hai."
        )

    # Read audio
    audio_bytes = audio.file.read(MAX_AUDIO_BYTES + 1)

    if not audio_bytes:
        raise HTTPException(
            status_code=400,
            detail="Audio empty hai. Dobara record karo."
        )

    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Recording 20 MB se chhoti honi chahiye."
        )

    filename = audio.filename or "recording.webm"
    content_type = audio.content_type or "audio/webm"

    # STEP 1: ELEVENLABS STT
    stt_start = time.perf_counter()

    try:
        stt_response = elevenlabs_session.post(
            "https://api.elevenlabs.io/v1/speech-to-text",
            headers={
                "xi-api-key": ELEVENLABS_API_KEY
            },
            data={
                "model_id": "scribe_v2"
            },
            files={
                "file": (
                    filename,
                    audio_bytes,
                    content_type
                )
            },
            timeout=120
        )

        stt_response.raise_for_status()
        payload = stt_response.json()

        transcript = (
            payload.get("text", "").strip()
            if isinstance(payload, dict)
            else ""
        )

    except requests.RequestException as exc:
        logger.exception("ELEVENLABS_REQUEST_FAILED")

        detail = "ElevenLabs transcription request fail hui."

        if getattr(exc, "response", None) is not None:
            detail += (
                f" Status: {exc.response.status_code}; "
                f"response: {exc.response.text[:500]}"
            )

        raise HTTPException(
            status_code=502,
            detail=detail
        ) from exc

    except ValueError as exc:
        logger.exception("ELEVENLABS_INVALID_RESPONSE")

        raise HTTPException(
            status_code=502,
            detail="ElevenLabs ne valid response nahi diya."
        ) from exc

    stt_latency = round(
        (time.perf_counter() - stt_start) * 1000, 2
    )

    if not transcript:
        raise HTTPException(
            status_code=422,
            detail="मैं आपकी बात साफ़ नहीं सुन पाई। कृपया दोबारा बोलें।"
        )

    hindi_input = any("\u0900" <= character <= "\u097f" for character in transcript)

    # STEP 2: GROQ LLM
    groq_start = time.perf_counter()

    try:
        completion = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a fast, helpful voice assistant. "
                        + (
                            "The user is speaking Hindi. Reply only in natural Hindi written in Devanagari. "
                            "If unclear, ask them to repeat in Hindi. "
                            if hindi_input else
                            "Reply in the user's language. If unclear, ask them to repeat in that language. "
                        )
                        + "If you do not know, say so plainly and do not invent facts. "
                        + "Never mention internal tools, model names, empty responses, or technical errors. "
                        + "Answer in 1-2 short sentences without Markdown."
                    )
                },
                {
                    "role": "user",
                    "content": transcript
                }
            ],
            temperature=0.4,
            max_completion_tokens=80,
            reasoning_effort="low"
        )

        answer = (
            completion.choices[0].message.content or ""
        ).strip()

    except Exception as exc:
        logger.exception("GROQ_REQUEST_FAILED")
        answer = (
            "अभी मैं जवाब तैयार नहीं कर पाई। कृपया थोड़ी देर बाद फिर पूछें।"
            if hindi_input
            else "I couldn't prepare an answer just now. Please try again shortly."
        )

    groq_latency = round(
        (time.perf_counter() - groq_start) * 1000, 2
    )

    if not answer:
        answer = (
            "मैं आपकी बात ठीक से समझ नहीं पाई। कृपया एक बार फिर बोलें।"
            if hindi_input
            else "I didn't understand that clearly. Could you please say it again?"
        )

    # STEP 3: SQL DATABASE
    db_start = time.perf_counter()

    try:
        conversation = Conversation(
            user_message=transcript,
            ai_response=answer,
            model=GROQ_MODEL
        )

        db.add(conversation)
        db.commit()
        db.refresh(conversation)

    except Exception as exc:
        db.rollback()
        logger.exception("DATABASE_SAVE_FAILED")

        raise HTTPException(
            status_code=500,
            detail="Conversation SQL database mein save nahi hui."
        ) from exc

    db_latency = round(
        (time.perf_counter() - db_start) * 1000, 2
    )

    # STEP 4: TOTAL BACKEND LATENCY
    # This endpoint receives audio and performs STT on the backend,
    # so its total naturally includes STT, Groq and database work.
    total_latency = round(
        (time.perf_counter() - total_start) * 1000, 2
    )

    logger.info(
        "VOICE_REQUEST_SUCCESS | "
        "QUESTION=%r | ANSWER=%r | MODEL=%s | "
        "TOOLS=ElevenLabs STT, Groq LLM, SQL Database, "
        "Browser SpeechSynthesis | "
        "STT_LATENCY_MS=%.2f | "
        "GROQ_LATENCY_MS=%.2f | "
        "DB_LATENCY_MS=%.2f | "
        "TOTAL_BACKEND_LATENCY_MS=%.2f | "
        "DATABASE_SAVED=True",
        transcript,
        answer,
        GROQ_MODEL,
        stt_latency,
        groq_latency,
        db_latency,
        total_latency
    )

    return {
        "id": conversation.id,
        "transcript": transcript,
        "answer": answer,
        "model": GROQ_MODEL,
        "saved_to_database": True
    }
