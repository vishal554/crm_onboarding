"""Duplicate ticket detection across email / phone / document number / hash."""

from django.db.models import Q

from onboarding.models import Ticket


def find_duplicate(ticket):
    """Return the earliest *other* ticket that looks like the same applicant.

    Matches on applicant email, phone, an extracted document number, or a shared
    document content hash (the same identity-document bytes resubmitted under a
    different applicant - per-ticket dedup can't catch this cross-ticket case).
    """
    q = Q()
    if ticket.applicant_email:
        q |= Q(applicant_email__iexact=ticket.applicant_email)
    if ticket.applicant_phone:
        q |= Q(applicant_phone=ticket.applicant_phone)

    doc_number = (ticket.parsed_source or {}).get("document", {}).get("document_number")
    if doc_number:
        q |= Q(parsed_source__document__document_number=doc_number)

    doc_hashes = list(ticket.documents.values_list("sha256", flat=True))
    if doc_hashes:
        q |= Q(documents__sha256__in=doc_hashes)

    if not q:
        return None

    # A ticket can only duplicate one that already existed (lower id), so the
    # first of a pair stays clean and the later submission is the duplicate.
    # distinct() collapses the multiple rows the documents join can produce.
    return (
        Ticket.objects.filter(q)
        .filter(id__lt=ticket.id)
        .order_by("created_at")
        .distinct()
        .first()
    )
