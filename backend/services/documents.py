"""Reading a claim's paperwork with Qwen-VL.

The same contract as the explanation layer: Qwen reads, the clerk confirms, the model
decides.

* Qwen-VL turns photos or PDFs of the bill and its supporting documents into the fields
  the check already uses. It may only choose from the vocabularies the model was trained
  on, and every value goes through `validate_answer` exactly as if the clerk had typed
  it; anything that fails is dropped, and the check asks for it instead.
* Documents settle what is *printed* on them: the patient, the procedure, the amount, the
  insurer, the plan, panel status, the diagnosis, the treatment date, whether it was an
  emergency. They never settle what a document can only suggest — whether
  pre-authorisation was obtained, whether the paperwork is complete. Those stay
  questions, chosen by the engine as before; the reading supplies the evidence shown
  beside them. A missing approval letter is not proof that approval was never given.
* Nothing identifying is asked for or kept: no names, ID or phone numbers. Files are
  decoded and re-encoded in memory (which also drops photo metadata), sent once, and
  never written to disk. Only the extracted fields are remembered, for half an hour.
"""

from __future__ import annotations

import io
import json
import logging
import re
import threading
import time
import uuid
from base64 import b64encode
from collections import deque
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache

import pypdfium2 as pdfium
from openai import OpenAI
from PIL import Image, ImageOps

from backend.config import DashScopeConfig, load_dashscope_config
from backend.predictor import Predictor
from backend.services.adaptive import (VALUE_LABELS, InvalidAnswer, feature_label,
                                       validate_answer, value_label)

logger = logging.getLogger(__name__)

MAX_FILES = 3
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_PAGES = 4                   # across all files; one call, so it stays inside the timeout
MAX_SIDE = 1600                 # pixels; legible for print, and bounds the image tokens
MAX_PIXELS = 40_000_000         # refuse decompression bombs before decoding them
# A page of print takes Qwen-VL a few seconds; this stays under the web proxy's 30 s.
TIMEOUT_S = 25.0
CALLS_PER_MINUTE = 10           # across all users: each call carries several images
READING_TTL_S = 30 * 60
MAX_READINGS = 200

DOCUMENT_TYPES = {
    "bill": "Itemised bill",
    "pre_auth_letter": "Pre-authorisation approval",
    "discharge_summary": "Discharge summary",
    "prescription": "Prescription",
    "lab_report": "Lab or imaging report",
    "insurance_card": "Insurance card",
    "other": "Other document",
}

# Printed on the paperwork, so a document can settle them (once the clerk confirms).
CONTEXT_FIELDS = ("patient_age", "patient_gender", "procedure_category", "claim_amount_pkr")
FACT_FIELDS = ("insurer_tpa", "plan_tier", "provider_network_status", "diagnosis_category",
               "is_emergency", "days_since_treatment")
# Only ever suggested: the clerk answers these, with the evidence in front of them.
HINT_FIELDS = ("pre_auth_obtained",)


class DocumentError(ValueError):
    """The upload itself is unusable. The message tells the clerk what to do."""


class ReaderUnavailable(RuntimeError):
    """The documents could not be read right now. The message tells the clerk what to do."""


@dataclass
class ReadValue:
    feature: str
    value: object                   # validated; None for a hint with nothing to suggest
    value_label: str
    evidence: str                   # a short quote from the document, or what is missing
    document: int | None = None     # 1-based index of the file it came from


@dataclass
class DocumentSummary:
    """One uploaded file: what it is, and the facts the cross-document checks compare."""

    index: int
    type: str
    label: str
    date: str | None = None
    amount_pkr: int | None = None
    patient_age: int | None = None
    patient_gender: str | None = None
    procedure: str | None = None
    diagnosis: str | None = None


@dataclass
class Reading:
    id: str
    context: dict[str, ReadValue]
    facts: dict[str, ReadValue]
    hints: dict[str, ReadValue]
    documents: list[DocumentSummary]
    notes: list[str] = field(default_factory=list)
    model: str | None = None


# ---------------------------------------------------------------------------
# Files -> page images
# ---------------------------------------------------------------------------

def _kind(data: bytes) -> str | None:
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image"
    return None


def _jpeg(img: Image.Image) -> bytes:
    img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)   # a fresh encode carries no EXIF or GPS data
    return out.getvalue()


def _image_pages(data: bytes, name: str) -> list[bytes]:
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.width * img.height > MAX_PIXELS:
                raise DocumentError(f"{name} is too large an image. Use a smaller photo.")
            return [_jpeg(ImageOps.exif_transpose(img))]   # phone photos arrive rotated
    except DocumentError:
        raise
    except Exception:  # noqa: BLE001 — any decoder failure is "unreadable" to the clerk
        raise DocumentError(f"{name} could not be opened as an image.")


def _pdf_pages(data: bytes, name: str, room: int) -> tuple[list[bytes], int]:
    try:
        pdf = pdfium.PdfDocument(data)
    except Exception:  # noqa: BLE001
        raise DocumentError(f"{name} could not be opened. It may be damaged or password-protected.")
    try:
        total = len(pdf)
        pages = []
        for i in range(min(total, room)):
            page = pdf[i]
            w, h = page.get_size()
            scale = min(MAX_SIDE / max(w, h, 1), 3.0)
            pages.append(_jpeg(page.render(scale=scale).to_pil()))
        return pages, total
    finally:
        pdf.close()


def prepare_pages(files: list[tuple[str, bytes]]) -> tuple[list[tuple[int, bytes]], list[str]]:
    """Each file as JPEG pages, tagged with its 1-based file number, plus any notes."""
    if not files:
        raise DocumentError("Add at least one document.")
    if len(files) > MAX_FILES:
        raise DocumentError(f"Add at most {MAX_FILES} documents at a time.")
    pages: list[tuple[int, bytes]] = []
    notes: list[str] = []
    for n, (name, data) in enumerate(files, 1):
        if len(data) > MAX_FILE_BYTES:
            raise DocumentError(f"{name} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB.")
        room = MAX_PAGES - len(pages)
        if room <= 0:
            notes.append(f"Only the first {MAX_PAGES} pages were read; {name} was skipped.")
            continue
        kind = _kind(data)
        if kind == "pdf":
            got, total = _pdf_pages(data, name, room)
            if total > len(got):
                notes.append(f"Only the first {len(got)} of {total} pages of {name} were read.")
        elif kind == "image":
            got = _image_pages(data, name)
        else:
            raise DocumentError(f"{name} is not a photo (JPEG, PNG, WebP) or a PDF.")
        pages.extend((n, p) for p in got)
    return pages, notes


# ---------------------------------------------------------------------------
# The call
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You read photographed or scanned medical claim documents for the claims \
desk of a private clinic. Report only what is printed on them, as JSON.

Rules you must never break:
1. Copy, never guess. If a value is not clearly printed, use null.
2. For fields with a list of options, answer with one option's value from the list, \
chosen by meaning, or null if none clearly applies.
3. Never write any person's name, ID or card number, phone number or address — not in \
any field and not in any evidence.
4. Evidence is a short quote, at most 12 words, copied from the document, that shows the \
value. Leave out names and numbers that identify a person.

Reply with the JSON object only: no markdown, no commentary."""


# What each diagnosis category covers. A bare label invites near-misses: "Urinary" drew
# gallstones, because stones.
DIAGNOSIS_SCOPE = {
    "renal": "kidney disease, kidney failure, dialysis patients",
    "genitourinary": "bladder, urinary tract, kidney stones, prostate, reproductive organs",
    "oncology": "any cancer or tumour",
    "cardiometabolic": "heart disease, hypertension, diabetes, cholesterol",
    "injury_trauma": "fractures, wounds, accidents, burns",
    "dental_oral": "teeth, gums, mouth",
    "pregnancy_maternity": "pregnancy, delivery, antenatal and postnatal care",
    "respiratory_infection": "chest and airway infections, pneumonia, flu",
    "infectious": "other infections such as typhoid, dengue, malaria, TB",
    "neurological": "brain, nerves, stroke, epilepsy",
    "mental_health": "depression, anxiety, psychiatric conditions",
    "allergy": "allergies and allergic reactions",
    "general_unspecified": "anything else, including digestive, liver and gallbladder "
                           "(gallstones), skin and eye conditions",
}


def _options_text(predictor: Predictor) -> str:
    lines = []
    for f in ("procedure_category", "diagnosis_category", "plan_tier", "provider_network_status"):
        labels = VALUE_LABELS.get(f, {})
        scope = DIAGNOSIS_SCOPE if f == "diagnosis_category" else {}
        opts = "; ".join(f"{v} = {labels.get(v, v)}" + (f" ({scope[v]})" if v in scope else "")
                         for v in predictor.categories[f])
        lines.append(f"{f}: {opts}")
    lines.append("insurer_tpa: " + "; ".join(predictor.categories["insurer_tpa"]))
    return "\n".join(lines)


def _user_prompt(n_files: int, predictor: Predictor) -> str:
    types = ", ".join(DOCUMENT_TYPES)
    return f"""There are {n_files} file(s); each page above is labelled with its file number.

Return this JSON object:
{{
  "documents": [one entry per file: {{"file": file number, "type": one of [{types}],
    "date": date of service, admission or approval as "YYYY-MM-DD" or null,
    "amount_pkr": the total billed on a bill, or the approved amount on a \
pre-authorisation, as a whole number, or null,
    "patient_age": whole number or null, "patient_gender": "M", "F" or null,
    "procedure": the procedure as printed, at most 8 words, or null,
    "diagnosis": the diagnosis as printed, at most 8 words, or null}}],
  "fields": {{
    each of the keys below, as {{"value": ..., "evidence": "...", "file": file number}}, \
or null when it is not on any document:
    "patient_age": whole number,
    "patient_gender": "M" or "F",
    "procedure_category": one option value,
    "claim_amount_pkr": the bill's total as a whole number of rupees,
    "treatment_date": "YYYY-MM-DD", the date the treatment was given,
    "insurer_tpa": one option value,
    "plan_tier": one option value,
    "provider_network_status": one option value,
    "diagnosis_category": one option value, using what each option covers,
    "is_emergency": true or false, only if the admission or visit type is printed,
    "pre_auth_reference": the pre-authorisation approval or reference number as printed, \
only if one is printed
  }}
}}

Options:
{_options_text(predictor)}"""


@lru_cache(maxsize=4)
def _client(api_key: str, base_url: str, workspace_id: str | None) -> OpenAI:
    headers = {"X-DashScope-WorkSpace": workspace_id} if workspace_id else None
    return OpenAI(api_key=api_key, base_url=base_url, default_headers=headers,
                  timeout=TIMEOUT_S, max_retries=0)


class _CallBudget:
    def __init__(self, per_minute: int):
        self.per_minute, self.calls, self.lock = per_minute, deque(), threading.Lock()

    def take(self) -> bool:
        now = time.monotonic()
        with self.lock:
            while self.calls and now - self.calls[0] > 60:
                self.calls.popleft()
            if len(self.calls) >= self.per_minute:
                return False
            self.calls.append(now)
            return True


_budget = _CallBudget(CALLS_PER_MINUTE)


def _call_model(config: DashScopeConfig, pages: list[tuple[int, bytes]], prompt: str) -> str:
    content: list[dict] = []
    for n, jpeg in pages:
        content.append({"type": "text", "text": f"File {n}:"})
        content.append({"type": "image_url", "image_url": {
            "url": "data:image/jpeg;base64," + b64encode(jpeg).decode()}})
    content.append({"type": "text", "text": prompt})
    response = _client(config.api_key, config.base_url, config.workspace_id).chat.completions.create(
        model=config.vl_model,
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user", "content": content}],
        temperature=0,
        max_tokens=1200,
    )
    return response.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# Reply -> validated fields
# ---------------------------------------------------------------------------

# A run of nine or more digits (spaces and dashes allowed) is an ID, card, phone or
# account number: a CNIC has 13, a mobile number 11. A date has eight, so it survives.
_DIGIT_RUN = re.compile(r"\+?\d[\d \-]*\d")


def _redact(text: str) -> str:
    return _DIGIT_RUN.sub(lambda m: "•••" if sum(c.isdigit() for c in m.group(0)) >= 9
                          else m.group(0), text)


def _clean_text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = _redact(" ".join(value.split()))
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _whole(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        t = value.strip().removeprefix("PKR").removeprefix("Rs.").removeprefix("Rs").strip()
        if re.fullmatch(r"\d+|\d{1,3}(,\d{3})+", t):
            return int(t.replace(",", ""))
    return None


def _date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _option(feature: str, value: object, predictor: Predictor) -> object:
    """Accept an option's value, or (forgivingly) its label; anything else stays as given
    and fails validation."""
    if not isinstance(value, str):
        return value
    allowed = predictor.categories.get(feature, [])
    if value in allowed:
        return value
    by_label = {str(label).lower(): v for v, label in VALUE_LABELS.get(feature, {}).items()}
    by_value = {str(v).lower(): v for v in allowed}
    key = value.strip().lower()
    return by_value.get(key, by_label.get(key, value))


def _coerce(feature: str, value: object, predictor: Predictor, today: date) -> object:
    if feature in ("patient_age", "claim_amount_pkr"):
        return _whole(value)
    if feature == "patient_gender":
        if isinstance(value, str):
            return {"m": "M", "male": "M", "f": "F", "female": "F"}.get(value.strip().lower(), value)
        return value
    if feature == "is_emergency":
        if isinstance(value, str):
            return {"true": True, "yes": True, "false": False, "no": False}.get(value.strip().lower(), value)
        return value
    if feature == "days_since_treatment":
        d = _date(value)
        return None if d is None else (today - d).days
    return _option(feature, value, predictor)


def interpret(raw: dict, predictor: Predictor, n_files: int, today: date) -> Reading:
    """Turn Qwen's JSON into validated fields. Pure, so it is tested without the network."""
    fields_in = raw.get("fields") if isinstance(raw.get("fields"), dict) else {}
    notes: list[str] = []

    def entry(key: str) -> tuple[object, str, int | None] | None:
        item = fields_in.get(key)
        if not isinstance(item, dict) or item.get("value") is None:
            return None
        file_no = _whole(item.get("file"))
        file_no = file_no if file_no and 1 <= file_no <= n_files else None
        return item["value"], _clean_text(item.get("evidence"), 100) or "", file_no

    context: dict[str, ReadValue] = {}
    facts: dict[str, ReadValue] = {}
    for feature in (*CONTEXT_FIELDS, *FACT_FIELDS):
        got = entry("treatment_date" if feature == "days_since_treatment" else feature)
        if got is None:
            continue
        value, evidence, file_no = got
        coerced = _coerce(feature, value, predictor, today)
        if feature == "days_since_treatment" and isinstance(coerced, int) and coerced < 0:
            notes.append("The treatment date on the documents is in the future, so it was not used.")
            continue
        try:
            checked = validate_answer(feature, coerced, predictor)
        except InvalidAnswer:
            notes.append(f"{feature_label(feature)} could not be read reliably, so the check "
                         "will ask for it.")
            continue
        target = context if feature in CONTEXT_FIELDS else facts
        target[feature] = ReadValue(feature, checked, value_label(feature, checked), evidence, file_no)

    documents = []
    listed = {(_whole(d.get("file")) or 0): d for d in raw.get("documents", [])
              if isinstance(d, dict)} if isinstance(raw.get("documents"), list) else {}
    for n in range(1, n_files + 1):
        d = listed.get(n, {})
        kind = d.get("type") if d.get("type") in DOCUMENT_TYPES else "other"
        when = _date(d.get("date"))
        amount = _whole(d.get("amount_pkr"))
        age = _whole(d.get("patient_age"))
        gender = _coerce("patient_gender", d.get("patient_gender"), predictor, today)
        documents.append(DocumentSummary(
            index=n, type=kind, label=DOCUMENT_TYPES[kind],
            date=when.isoformat() if when else None,
            amount_pkr=amount if amount and 0 < amount <= 50_000_000 else None,
            patient_age=age if age is not None and 0 <= age <= 120 else None,
            patient_gender=gender if gender in ("M", "F") else None,
            procedure=_clean_text(d.get("procedure"), 60),
            diagnosis=_clean_text(d.get("diagnosis"), 60)))

    # Pre-authorisation is only ever a suggestion: an approval letter or a printed
    # reference suggests "obtained"; their absence suggests nothing, and says so.
    letter = next((d for d in documents if d.type == "pre_auth_letter"), None)
    reference = entry("pre_auth_reference")
    if letter or reference:
        quote = reference[1] if reference and reference[1] else None
        evidence = ("The documents include a pre-authorisation approval"
                    + (f" (“{quote.rstrip('.')}”)." if quote else "."))
        hint = ReadValue("pre_auth_obtained", "yes", value_label("pre_auth_obtained", "yes"),
                         evidence, letter.index if letter else reference[2])
    else:
        hint = ReadValue("pre_auth_obtained", None, "", "No pre-authorisation approval or "
                         "reference was found among the documents.")
    return Reading(id=str(uuid.uuid4()), context=context, facts=facts,
                   hints={"pre_auth_obtained": hint}, documents=documents, notes=notes)


def parse_reply(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the reply")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("the reply is not a JSON object")
    return data


def read_documents(files: list[tuple[str, bytes]], predictor: Predictor,
                   config: DashScopeConfig | None = None, today: date | None = None) -> Reading:
    config = config or load_dashscope_config()
    if not config.is_configured:
        raise ReaderUnavailable("Reading documents needs the Qwen service, which is not set "
                                "up on this server. Enter the bill by hand instead.")
    pages, notes = prepare_pages(files)
    if not _budget.take():
        raise ReaderUnavailable("Too many documents are being read right now. Wait a moment, "
                                "or enter the bill by hand.")
    try:
        raw = parse_reply(_call_model(config, pages, _user_prompt(len(files), predictor)))
    except Exception as exc:  # noqa: BLE001 — a failed read must leave manual entry open
        logger.warning("Document reading failed (%s).", type(exc).__name__)
        raise ReaderUnavailable("The documents could not be read just now. Try again, or "
                                "enter the bill by hand.")
    reading = interpret(raw, predictor, len(files), today or date.today())
    reading.notes = notes + reading.notes
    reading.model = config.vl_model
    return reading


class ReadingStore:
    """Bounded and expiring, like the session store: a reading is only needed until the
    clerk starts the check from it."""

    def __init__(self, max_items: int = MAX_READINGS, ttl_s: float = READING_TTL_S):
        self.max_items, self.ttl_s = max_items, ttl_s
        self._items: dict[str, tuple[float, Reading]] = {}
        self._lock = threading.Lock()

    def put(self, reading: Reading) -> None:
        now = time.monotonic()
        with self._lock:
            for k in [k for k, (t, _) in self._items.items() if now - t > self.ttl_s]:
                del self._items[k]
            while len(self._items) >= self.max_items:
                del self._items[min(self._items, key=lambda k: self._items[k][0])]
            self._items[reading.id] = (now, reading)

    def get(self, reading_id: str) -> Reading | None:
        with self._lock:
            item = self._items.get(reading_id)
            if item is None or time.monotonic() - item[0] > self.ttl_s:
                return None
            return item[1]
