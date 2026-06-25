"""Unit tests for identity-document field extraction (no DB / OCR needed)."""

from onboarding.parsing.extractors import (
    extract_identity_fields,
    verhoeff_valid,
)


def test_aadhaar_name_and_dob_unlabelled():
    text = "Government of India\nRahul Sharma\nDOB: 15/08/1992\nMale\n2345 6789 0123"
    fields = extract_identity_fields(text)
    assert fields["name"] == "Rahul Sharma"
    assert fields["dob"] == "1992-08-15"
    assert fields["document_kind"] == "AADHAAR"


def test_pan_name_is_holder_not_father():
    text = (
        "INCOME TAX DEPARTMENT\nPRIYA VERMA\nFather Name\nRAJESH VERMA\n"
        "Date of Birth\n22/03/1988\nABCDE1234F"
    )
    fields = extract_identity_fields(text)
    assert fields["document_number"] == "ABCDE1234F"
    assert fields["document_kind"] == "PAN"
    assert fields["name"] == "PRIYA VERMA"


def test_dob_ignores_print_and_issue_date():
    text = "Issue Date : 01/01/2013\nPrint Date : 27/02/2021\nDOB : 01/04/1998\n6018 4112 2725"
    fields = extract_identity_fields(text)
    assert fields["dob"] == "1998-04-01"


def test_aadhaar_recovered_when_abutting_a_date():
    text = "Print Date : 27/02/2021 6018 4112 2725"
    fields = extract_identity_fields(text)
    assert fields["document_number"] == "601841122725"


def test_aadhaar_address_extracted_to_pin():
    text = (
        "Government of India\nRahul Sharma\nDOB: 15/08/1992\n"
        "Address: S/O Mohan Sharma, 45 Park Street,\n"
        "Andheri West, Mumbai, Maharashtra - 400058\n2345 6789 0123"
    )
    fields = extract_identity_fields(text)
    assert fields["address"] == (
        "S/O Mohan Sharma, 45 Park Street, Andheri West, "
        "Mumbai, Maharashtra - 400058"
    )


def test_pan_has_no_address():
    text = (
        "INCOME TAX DEPARTMENT\nPRIYA VERMA\nFather Name\nRAJESH VERMA\n"
        "Date of Birth\n22/03/1988\nABCDE1234F"
    )
    assert "address" not in extract_identity_fields(text)


def test_verhoeff_checksum():
    assert verhoeff_valid("601841122725") is True
    assert verhoeff_valid("202160184112") is False
