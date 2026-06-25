"""Unit tests for free-form email-body parsing (uses spaCy NER)."""

from onboarding.parsing.email_parser import (
    parse_email_body,
    parse_email_headers,
    parse_from_header,
)


def test_prose_extraction():
    body = (
        "Hi, I'm Rahul Sharma. Reach me at rahul@example.com or 9123456780. "
        "I stay at 45 Park Street, Mumbai."
    )
    r = parse_email_body(body)
    assert r["name"] == "Rahul Sharma"
    assert r["email"] == "rahul@example.com"
    assert r["phone"] == "9123456780"
    assert "Park Street" in r["address"]


def test_phone_with_internal_spaces():
    assert parse_email_body("call me on +91 98765 43210")["phone"] == "9876543210"


def test_phone_country_code_and_dashes_normalised():
    # libphonenumber parses +91, dashes and grouping to the national number.
    assert parse_email_body("reach me at +91-91234-56780")["phone"] == "9123456780"


def test_aadhaar_like_digits_not_mistaken_for_phone():
    # A 12-digit Aadhaar-style run must not be returned as a phone number.
    assert parse_email_body("My Aadhaar is 2345 6789 0123, thanks.")["phone"] == ""


def test_email_trailing_punctuation_stripped():
    assert parse_email_body("write to priya.v@gmail.com.")["email"] == "priya.v@gmail.com"


def test_labelled_fields_take_precedence():
    r = parse_email_body("Name: John Doe\nPhone: 9000011122")
    assert r["name"] == "John Doe"
    assert r["phone"] == "9000011122"


def test_parse_headers_from_body():
    h = parse_email_headers("Message-ID: <a@b>\nIn-Reply-To: <c@d>\n\nhello there")
    assert h["message_id"] == "<a@b>"
    assert h["in_reply_to"] == "<c@d>"


def test_parse_from_header_name_and_address():
    assert parse_from_header("Rahul Sharma <rahul@example.com>") == (
        "Rahul Sharma",
        "rahul@example.com",
    )
    assert parse_from_header("bare@example.com") == ("", "bare@example.com")


def test_message_id_not_picked_as_applicant_email():
    # The Message-ID value looks like an address but must not become the email;
    # the real address comes from the From header.
    body = (
        "Message-ID: <onboard-meera-100@example.com>\n"
        "In-Reply-To: <thread-99@example.com>\n"
        "From: Meera Iyer <meera.iyer@example.com>\n\n"
        "Following up on my onboarding request."
    )
    assert parse_email_body(body)["email"] == "meera.iyer@example.com"


def test_name_and_email_fall_back_to_from_header():
    # No name/email stated in the prose - both come "from mail".
    body = "From: Rahul Sharma <rahul@example.com>\n\nPlease onboard me."
    r = parse_email_body(body)
    assert r["name"] == "Rahul Sharma"
    assert r["email"] == "rahul@example.com"
