"""Extract structured identity fields from OCR text (Aadhaar / PAN)."""

import re
from datetime import date, datetime

# A run of digits possibly separated by space/tab/dash on one line (never a
# newline - that let an Aadhaar number span across an adjacent date).
DIGIT_RUN_RE = re.compile(r"\d[\d \t-]*\d")
PAN_RE = re.compile(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b")

# Verhoeff checksum tables - real Aadhaar numbers satisfy this checksum, so it
# lets us pick the genuine 12-digit candidate over an OCR false-match.
_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


def verhoeff_valid(number: str) -> bool:
    """True if a 12-digit string satisfies the Verhoeff checksum (Aadhaar)."""
    if len(number) != 12 or not number.isdigit():
        return False
    c = 0
    for i, digit in enumerate(reversed(number)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(digit)]]
    return c == 0


def _extract_aadhaar(text: str):
    """Find a 12-digit Aadhaar number.

    Each single-line digit run is scanned with a sliding 12-digit window so a
    number abutting an adjacent date can still be recovered; the Verhoeff-valid
    window is preferred, falling back to the first 12-digit candidate.
    """
    candidates = []
    for run in DIGIT_RUN_RE.findall(text):
        digits = re.sub(r"\D", "", run)
        if 12 <= len(digits) <= 24:
            for i in range(len(digits) - 11):
                candidates.append(digits[i : i + 12])
        elif len(digits) == 12:
            candidates.append(digits)

    # UIDAI Aadhaar numbers never start with 0 or 1.
    candidates = [c for c in candidates if c[0] in "23456789"]
    if not candidates:
        return None
    for c in candidates:
        if verhoeff_valid(c):
            return c
    return candidates[0]  # best effort when OCR noise fails the checksum
DOB_RE = re.compile(r"\b(\d{2}[/-]\d{2}[/-]\d{4})\b")
# Only treat a date as DOB when it follows a birth-date label - never an
# Issue Date / Print Date / Download Date that also appears on the card.
DOB_LABEL_RE = re.compile(
    r"(?:DOB|date\s+of\s+birth|year\s+of\s+birth|जन्म[^\n]*?)\D{0,15}"
    r"(\d{2}[/-]\d{2}[/-]\d{4})",
    re.IGNORECASE,
)
NAME_LABEL_RE = re.compile(r"\bname\s*[:\-]\s*(.+)", re.IGNORECASE)
# Anchors that typically sit right after the name on an ID card.
DOB_ANCHOR_RE = re.compile(r"\b(DOB|date of birth|year of birth|जन्म)\b", re.IGNORECASE)
# On a PAN card the applicant's name sits just above the father/guardian marker.
PARENT_ANCHOR_RE = re.compile(r"\b(father|husband|guardian|S/O|D/O|W/O)\b", re.IGNORECASE)

# Words that appear on ID cards but are never the applicant's name.
_NAME_STOPWORDS = {
    "government", "india", "govt", "unique", "identification", "authority",
    "male", "female", "dob", "birth", "year", "income", "tax", "department",
    "permanent", "account", "number", "card", "father", "fathers", "husband",
    "name", "aadhaar", "aadhar", "uidai", "signature", "address", "gender",
}


def _looks_like_name(line: str) -> bool:
    """Heuristic: a short, alphabetic, non-keyword line is probably a name."""
    line = line.strip()
    if not line or any(ch.isdigit() for ch in line):
        return False
    words = line.split()
    if not (1 <= len(words) <= 4):
        return False
    if not all(re.fullmatch(r"[A-Za-z.'-]+", w) for w in words):
        return False
    lowered = line.lower()
    if any(sw in lowered.split() for sw in _NAME_STOPWORDS):
        return False
    return len(re.sub(r"[^A-Za-z]", "", line)) >= 3


def _extract_name(text: str) -> str:
    """Find the applicant name from OCR text.

    Strategy: explicit ``Name:`` label first; otherwise the name on a real ID
    card sits on its own line just above the date-of-birth line, so fall back to
    the nearest name-like line before the DOB; finally the first name-like line.
    """
    label = NAME_LABEL_RE.search(text)
    if label:
        candidate = label.group(1).strip()
        if candidate:
            return candidate

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # PAN layout: applicant name is the name-line just above the father marker
    # (the line right before DOB there is the *father's* name, not the holder's).
    parent_index = next(
        (i for i, ln in enumerate(lines) if PARENT_ANCHOR_RE.search(ln)), None
    )
    if parent_index is not None:
        for j in range(parent_index - 1, -1, -1):
            if _looks_like_name(lines[j]):
                return lines[j]

    # Aadhaar layout: name sits on the line just above the DOB.
    dob_index = next(
        (i for i, ln in enumerate(lines) if DOB_RE.search(ln) or DOB_ANCHOR_RE.search(ln)),
        None,
    )
    if dob_index is not None:
        for j in range(dob_index - 1, -1, -1):
            if _looks_like_name(lines[j]):
                return lines[j]

    for ln in lines:
        if _looks_like_name(ln):
            return ln
    return ""


# Aadhaar carries the holder's address (PAN does not). It follows an
# "Address" / "पता" label and conventionally ends at the 6-digit PIN code.
ADDRESS_LABEL_RE = re.compile(r"(?:address|पता)\s*[:\-]?\s*", re.IGNORECASE)
PIN_RE = re.compile(r"\b\d{6}\b")


def _extract_address(text: str) -> str:
    """Read the holder's address from an Aadhaar OCR dump, if present.

    Captures from the address label up to and including the trailing PIN code
    (the natural end of an Indian postal address); when no PIN is found, falls
    back to the next few lines. Whitespace/newlines are collapsed to one line.
    """
    label = ADDRESS_LABEL_RE.search(text)
    if not label:
        return ""
    tail = text[label.end():]
    pin = PIN_RE.search(tail)
    chunk = tail[: pin.end()] if pin else "\n".join(tail.splitlines()[:4])
    return re.sub(r"\s+", " ", chunk).strip(" ,;:-")


def _normalise_dob(raw: str):
    raw = raw.replace("-", "/")
    for fmt in ("%d/%m/%Y",):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def compute_age(dob: date) -> int:
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def extract_identity_fields(text: str) -> dict:
    """Return whatever identity fields can be read from the OCR text."""
    fields: dict = {}

    pan = PAN_RE.search(text)
    aadhaar = _extract_aadhaar(text)
    if pan:
        fields["document_number"] = pan.group(1)
        fields["document_kind"] = "PAN"
    elif aadhaar:
        fields["document_number"] = aadhaar
        fields["document_kind"] = "AADHAAR"

    name = _extract_name(text)
    if name:
        fields["name"] = name

    address = _extract_address(text)
    if address:
        fields["address"] = address

    dob_match = DOB_LABEL_RE.search(text)
    if dob_match:
        dob = _normalise_dob(dob_match.group(1))
        if dob:
            fields["dob"] = dob.isoformat()
            fields["age"] = compute_age(dob)

    return fields
