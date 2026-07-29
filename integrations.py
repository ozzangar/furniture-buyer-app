"""Level-2 external-service integrations, kept out of app.py.

Each is self-contained and degrades gracefully when a key/credential is absent,
so the app runs (and demos) with or without external accounts configured.
"""
from __future__ import annotations

import os
import smtplib
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import boto3

_BEDROCK_REGION = os.environ.get("AWS_REGION", "ap-southeast-2")
_VISION_MODEL = os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-3-5-sonnet-20241022-v2:0")
_bedrock_client = None


def _bedrock():
    global _bedrock_client
    if _bedrock_client is None:
        _bedrock_client = boto3.client("bedrock-runtime", region_name=_BEDROCK_REGION)
    return _bedrock_client


# --- OCR / extract-from-image via Claude vision (Bedrock) — no extra key -----
def ocr_image(image_bytes: bytes, image_format: str = "jpeg",
              instruction: str = "Extract all text and key details from this image. "
                                  "If it's a receipt or invoice, list items and prices.") -> str:
    """Use Claude vision to read/describe an uploaded image. Returns extracted text."""
    fmt = image_format.lower().replace("jpg", "jpeg")
    resp = _bedrock().converse(
        modelId=_VISION_MODEL,
        messages=[{"role": "user", "content": [
            {"text": instruction},
            {"image": {"format": fmt, "source": {"bytes": image_bytes}}},
        ]}],
        inferenceConfig={"maxTokens": 1024},
    )
    return "".join(b.get("text", "") for b in resp["output"]["message"]["content"]).strip()


# --- Calendar invite (.ics) — pure Python, no external service --------------
def build_ics(summary: str, description: str, start: datetime,
              duration_minutes: int = 60, location: str = "") -> str:
    """Return a valid iCalendar (.ics) document as text. No dependencies."""
    def stamp(dt: datetime) -> str:
        return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    uid = uuid.uuid4().hex + "@boakea"
    end = start + timedelta(minutes=duration_minutes)
    # Escape per RFC 5545 (commas, semicolons, newlines).
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")

    return "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Boakea//Furniture//EN",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{stamp(datetime.now(timezone.utc))}",
        f"DTSTART:{stamp(start)}",
        f"DTEND:{stamp(end)}",
        f"SUMMARY:{esc(summary)}",
        f"DESCRIPTION:{esc(description)}",
        f"LOCATION:{esc(location)}",
        "END:VEVENT", "END:VCALENDAR", "",
    ])


# --- Audio transcription — external API if a key is set, else graceful no-op -
def transcription_configured() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def transcribe_audio(audio_bytes: bytes, filename: str = "audio.webm") -> dict:
    """Transcribe speech to text via OpenAI Whisper API if OPENAI_API_KEY is set.

    Returns {ok, text|message}. Bedrock has no speech-to-text, so this needs an
    external API; without a key it returns a clear, non-crashing message.
    """
    if not transcription_configured():
        return {"ok": False,
                "message": "Audio transcription needs an API key (set OPENAI_API_KEY). "
                           "Received the audio but did not transcribe."}
    try:
        import requests
        r = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            files={"file": (filename, audio_bytes)},
            data={"model": "whisper-1"},
            timeout=60,
        )
        if r.status_code != 200:
            return {"ok": False, "message": f"Transcription failed ({r.status_code})."}
        return {"ok": True, "text": r.json().get("text", "")}
    except Exception as e:
        return {"ok": False, "message": f"Transcription error: {type(e).__name__}: {e}"}


# --- Send email — SMTP if configured, else a graceful no-op -----------------
def email_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_FROM"))


def send_email(to_addr: str, subject: str, body: str,
               attachment: tuple[str, bytes, str] | None = None) -> dict:
    """Send an email via SMTP env config. Returns {sent, message}.

    Env: SMTP_HOST, SMTP_PORT(=587), SMTP_USER, SMTP_PASS, SMTP_FROM.
    If not configured, returns sent=False with a clear message (no crash) so the
    feature is demoable and the wiring is proven even without a mail account.
    """
    if not email_configured():
        return {"sent": False,
                "message": "Email not configured (set SMTP_HOST/SMTP_FROM). "
                           "Composed the message but did not send."}
    msg = EmailMessage()
    msg["From"] = os.environ["SMTP_FROM"]
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    if attachment:
        fname, data, mime = attachment
        maintype, _, subtype = mime.partition("/")
        msg.add_attachment(data, maintype=maintype or "application", subtype=subtype or "octet-stream",
                           filename=fname)
    try:
        host = os.environ["SMTP_HOST"]
        port = int(os.environ.get("SMTP_PORT", "587"))
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls()
            if os.environ.get("SMTP_USER"):
                s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASS", ""))
            s.send_message(msg)
        return {"sent": True, "message": f"Email sent to {to_addr}."}
    except Exception as e:
        return {"sent": False, "message": f"Email send failed: {type(e).__name__}: {e}"}
