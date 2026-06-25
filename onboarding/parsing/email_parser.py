"""Extraction of applicant fields from a free-form email body.

Email bodies are prose, not neat ``Label: value`` lines, e.g.:

    "Hi, I'm Rahul Sharma and I'd like to onboard. You can reach me at
     rahul@example.com or 9123456780. I currently stay at 45 Park Street,
     Mumbai."

Strategy:
* email / phone  - regex (reliable even inside prose)
* name           - spaCy PERSON entity
* address        - a "lives at ..." phrase if present, else spaCy location
                   entities (GPE/LOC/FAC)
Explicit ``Label: value`` lines, when present, always take precedence.
"""

import re

import phonenumbers

# Default region for parsing local (non-+country-code) numbers.
DEFAULT_PHONE_REGION = "IN"

# Indian mobile numbers: optional +91/0 prefix, then 6-9 followed by 9 digits.
PHONE_RE = re.compile(r"(?<!\d)(?:\+91[-\s]?|0)?([6-9]\d{9})(?!\d)")
# Looser candidate: digit runs that may contain spaces/dashes (e.g. 98765 43210).
PHONE_CANDIDATE_RE = re.compile(r"(?<!\d)(?:\+?91[-\s]?)?[6-9][\d\s-]{8,13}\d(?!\d)")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w-]+")
# Header lines whose values look like addresses but are message identifiers,
# not the applicant's email - scrubbed before scanning the body for an address.
_ID_HEADER_LINE_RE = re.compile(
    r"^\s*(?:Message-ID|In-Reply-To|References)\s*:.*$", re.IGNORECASE | re.MULTILINE
)
ADDRESS_PHRASE_RE = re.compile(
    r"(?:live[sd]?|living|stay(?:ing)?|reside[sd]?|residing|located|address)\s*"
    r"(?:at|in|is|:|=)?\s*([^.\n]+)",
    re.IGNORECASE,
)
# Name introductions / sign-offs that NER sometimes misses.
NAME_PHRASE_RE = re.compile(
    r"(?:my name is|i am|i'm|this is|regards|thanks|sincerely|best regards|yours)"
    r"[,\s]+([A-Z][a-zA-Z.'-]+(?:\s+[A-Z][a-zA-Z.'-]+){0,2})",
    re.IGNORECASE,
)

# Email headers, when present inline in the body (e.g. a forwarded / quoted
# message). Threading headers map replies to the original ticket; the From
# header carries the sender's name + address ("name/email from mail").
_HEADER_RES = {
    "message_id": re.compile(r"^\s*Message-ID\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    "in_reply_to": re.compile(r"^\s*In-Reply-To\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    "references": re.compile(r"^\s*References\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    "from": re.compile(r"^\s*From\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE),
}

# "Display Name <addr@host>" -> ("Display Name", "addr@host").
_FROM_NAME_EMAIL_RE = re.compile(r'^\s*"?([^"<]*?)"?\s*<([^>]+)>\s*$')


def parse_email_headers(body: str) -> dict:
    """Pull Message-ID / In-Reply-To / References / From from inline body headers."""
    body = body or ""
    result = {}
    for key, rx in _HEADER_RES.items():
        m = rx.search(body)
        result[key] = m.group(1).strip() if m else ""
    return result


def parse_from_header(value: str) -> tuple[str, str]:
    """Split a From header value into ``(display_name, email_address)``.

    Handles both ``Rahul Sharma <rahul@example.com>`` and a bare address.
    """
    value = (value or "").strip()
    if not value:
        return "", ""
    m = _FROM_NAME_EMAIL_RE.match(value)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    em = EMAIL_RE.search(value)
    return "", (em.group(0).strip(".,;:)") if em else "")

_NLP = None


def _nlp():
    """Lazily load the spaCy model once per process."""
    global _NLP
    if _NLP is None:
        import spacy

        _NLP = spacy.load("en_core_web_sm")
    return _NLP


def _ner(body: str):
    try:
        return _nlp()(body)
    except Exception:
        return None


def _labelled(body: str, label: str) -> str:
    match = re.search(rf"\b{label}\s*[:\-=]\s*(.+)", body, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _ner_name(doc) -> str:
    if doc is None:
        return ""
    persons = [e.text.strip() for e in doc.ents if e.label_ == "PERSON"]
    return persons[0] if persons else ""


def _name_from_phrases(body: str) -> str:
    match = NAME_PHRASE_RE.search(body)
    return match.group(1).strip() if match else ""


def _extract_phone(body: str) -> str:
    """Find a phone number in free-form text.

    Prefers libphonenumber's validating matcher - it parses real-world formats
    (+91, spacing, dashes, international) and rejects look-alike digit runs (an
    Aadhaar fragment, an order id). The regex heuristic stays as a fallback for
    odd-but-plausible numbers libphonenumber declines to mark valid.
    """
    for match in phonenumbers.PhoneNumberMatcher(body or "", DEFAULT_PHONE_REGION):
        if phonenumbers.is_valid_number(match.number):
            return str(match.number.national_number)
    return _extract_phone_regex(body)


def _extract_phone_regex(body: str) -> str:
    m = PHONE_RE.search(body)
    if m:
        return m.group(1)
    # Fallback: normalise a spaced/dashed candidate down to a 10-digit number.
    for cand in PHONE_CANDIDATE_RE.findall(body):
        digits = re.sub(r"\D", "", cand)
        if len(digits) == 12 and digits.startswith("91"):
            digits = digits[2:]
        elif len(digits) == 11 and digits.startswith("0"):
            digits = digits[1:]
        if len(digits) == 10 and digits[0] in "6789":
            return digits
    return ""


def _extract_email(body: str, from_addr: str) -> str:
    # Drop Message-ID/In-Reply-To/References lines first - their values look
    # like addresses but are not the applicant's email.
    scrubbed = _ID_HEADER_LINE_RE.sub("", body or "")
    m = EMAIL_RE.search(scrubbed)
    if m:
        return m.group(0).strip(".,;:)")
    return from_addr or ""


def _ner_address(body: str, doc) -> str:
    phrase = ADDRESS_PHRASE_RE.search(body)
    if phrase:
        return phrase.group(1).strip(" ,;")
    if doc is not None:
        locs = [e.text.strip() for e in doc.ents if e.label_ in ("GPE", "LOC", "FAC")]
        # De-duplicate while preserving order.
        unique = list(dict.fromkeys(locs))
        if unique:
            return ", ".join(unique)
    return ""


def _normalise_phone(phone: str) -> str:
    """Normalise a phone string to its national number (e.g. 9123456780)."""
    if not phone:
        return ""
    try:
        num = phonenumbers.parse(phone, DEFAULT_PHONE_REGION)
        if phonenumbers.is_valid_number(num):
            return str(num.national_number)
    except phonenumbers.NumberParseException:
        pass
    # Fallback for values libphonenumber can't validate: keep the last 10 digits.
    digits = re.sub(r"\D", "", phone)
    return digits[-10:] if len(digits) >= 10 else phone


def parse_email_body(body: str, from_addr: str = "") -> dict:
    body = body or ""

    # The inbound message's own From header ("name/email from mail"), used to
    # back-fill the applicant identity when the prose doesn't state it.
    from_name, from_email = parse_from_header(parse_email_headers(body).get("from", ""))

    # 1) Explicit labels win when present.
    name = _labelled(body, "name")
    phone = _labelled(body, "phone")
    address = _labelled(body, "address")
    email = _labelled(body, "email")

    # 2) email / phone via regex anywhere in the prose.
    if not email:
        email = _extract_email(body, from_addr or from_email)
    if not phone:
        phone = _extract_phone(body)

    # 3) name / address via NER (with phrase fallbacks) when not labelled.
    if not name or not address:
        doc = _ner(body)
        if not name:
            name = _ner_name(doc) or _name_from_phrases(body)
        if not address:
            address = _ner_address(body, doc)

    # 4) Sender's display name as the last resort for the applicant name.
    if not name:
        name = from_name

    return {
        "name": name,
        "phone": _normalise_phone(phone),
        "email": email,
        "address": address,
    }
