"""Validation workflow: runs checks and decides the ticket's terminal status.

Outcomes:
    * missing required documents            -> rejected
    * any mismatch / duplicate / bad format -> requires_manual_review
    * everything consistent                 -> approved
"""

import re

from onboarding.models import EventType, TicketStatus, ValidationResult
from onboarding.services.dedupe import find_duplicate


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _tokens(value: str) -> set[str]:
    """Meaningful (>=2 char) alphanumeric tokens of an address, lower-cased."""
    return {t for t in re.findall(r"[a-z0-9]+", (value or "").lower()) if len(t) >= 2}


def _address_consistent(email_addr: str, doc_addr: str) -> bool:
    """True when the user-typed address agrees with the Aadhaar address.

    Addresses are free-form and formatted differently in the email vs the card,
    so we compare on token overlap rather than exact text: the address is
    consistent when most of the user's address tokens appear on the document.
    """
    email_tokens = _tokens(email_addr)
    doc_tokens = _tokens(doc_addr)
    if not email_tokens or not doc_tokens:
        return True
    overlap = email_tokens & doc_tokens
    return len(overlap) / len(email_tokens) >= 0.5


def run_validation(ticket):
    """Run all checks, persist ValidationResult rows, return a decision tuple.

    Returns ``(status, event_type, message, duplicate_ticket)``.
    """
    results = []

    def record(name, passed, **detail):
        ValidationResult.objects.create(
            ticket=ticket, check_name=name, passed=passed, detail=detail
        )
        results.append((name, passed))

    documents = list(ticket.documents.all())
    has_doc = bool(documents)
    record("required_document_present", has_doc)

    format_ok = has_doc and all(
        d.metadata.get("format_valid", False) for d in documents
    )
    record("document_format_valid", format_ok, documents=len(documents))

    email_src = (ticket.parsed_source or {}).get("email", {})
    doc_src = (ticket.parsed_source or {}).get("document", {})

    name_match = True
    if email_src.get("name") and doc_src.get("name"):
        name_match = _norm(email_src["name"]) == _norm(doc_src["name"])
        record(
            "name_consistency",
            name_match,
            email=email_src["name"],
            document=doc_src["name"],
        )

    age_match = True
    if ticket.applicant_dob and doc_src.get("age") is not None and ticket.applicant_age:
        age_match = ticket.applicant_age == doc_src["age"]
        record("age_consistency", age_match)

    # Address is user-supplied in the email and verified against the Aadhaar.
    addr_match = True
    email_addr = email_src.get("address") or ticket.applicant_address
    if email_addr and doc_src.get("address"):
        addr_match = _address_consistent(email_addr, doc_src["address"])
        record(
            "address_consistency",
            addr_match,
            email=email_addr,
            document=doc_src["address"],
        )

    duplicate = find_duplicate(ticket)
    record(
        "duplicate_check",
        duplicate is None,
        duplicate_of=duplicate.ticket_ref if duplicate else None,
    )

    if not has_doc:
        return (
            TicketStatus.REJECTED,
            EventType.VALIDATION_FAILED,
            "Required identity documents missing",
            duplicate,
        )

    if not (format_ok and name_match and age_match and addr_match and duplicate is None):
        return (
            TicketStatus.REQUIRES_MANUAL_REVIEW,
            EventType.VALIDATION_FAILED,
            "Validation flagged the ticket for manual review",
            duplicate,
        )

    return (
        TicketStatus.APPROVED,
        EventType.VALIDATION_PASSED,
        "All validation checks passed",
        duplicate,
    )
