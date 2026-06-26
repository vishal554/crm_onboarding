"""Duplicate ticket detection.

A ticket is a duplicate of an earlier one only when *all the details they share*
agree - not on a single coincidental match. Concretely the identity must match
(same applicant **email and phone**), and when both tickets carry them the
**document number** and a shared **document hash** must agree too. Signals that
are absent on either side are skipped.
"""

from onboarding.models import Ticket


def find_duplicate(ticket):
    """Return the earliest *other* ticket that is the same applicant, or None."""
    # Identity is required: without both email and phone we can't assert a match.
    if not (ticket.applicant_email and ticket.applicant_phone):
        return None

    candidates = (
        Ticket.objects.filter(
            applicant_email__iexact=ticket.applicant_email,
            applicant_phone=ticket.applicant_phone,
        )
        .filter(id__lt=ticket.id)        # only an already-existing ticket can be the original
        .order_by("created_at")
    )

    doc_number = (ticket.parsed_source or {}).get("document", {}).get("document_number")
    doc_hashes = set(ticket.documents.values_list("sha256", flat=True))

    for cand in candidates:
        # If both tickets have a document number, it must match.
        cand_number = (cand.parsed_source or {}).get("document", {}).get("document_number")
        if doc_number and cand_number and doc_number != cand_number:
            continue
        # If both tickets have documents, they must share at least one hash.
        cand_hashes = set(cand.documents.values_list("sha256", flat=True))
        if doc_hashes and cand_hashes and doc_hashes.isdisjoint(cand_hashes):
            continue
        return cand

    return None
