# Sample emails

Four ready-to-run scenarios for `POST /api/email/inbound`. Each body is also saved
as a `.txt` file in this folder so you can submit it with newlines intact. Outcomes
below are **verified** against the running system.

Send the `body` as a form text field and any document as a file upload. With curl,
`-F "body=<file"` reads the body from a file and `-F "attachments=@file"` uploads a
document:

```bash
curl -s -X POST http://localhost:8000/api/email/inbound \
     -F "body=<samples/test1_match.txt" \
     -F "attachments=@samples/aadhaar_johndoe.png"
```

You can also paste any body into the Swagger `body` field at http://localhost:8000/api/docs.

> **Run order matters for tests 1 & 2** — both attach the same Aadhaar image, so
> whichever runs second is flagged as a duplicate identity document. Run test 1 first
> for a clean `approved`.

---

## 1 — Complete onboarding → `approved`
File: `test1_match.txt` · Attachment: `aadhaar_johndoe.png` (OCR reads *John Doe, DOB 01/01/1990*)

```
Name: John Doe
Email: john.doe@example.com
Phone: +91 98765 43210
Address: 45 Park Street, Andheri West, Mumbai 400058

Hi team, please find my Aadhaar attached to start onboarding.
```
Name/age match the document, the document is valid and unique → **approved**.
Notifications: `onboarding_received → documents_processed → validation_passed → ticket_approved`.

## 2 — Details don't match the document → `requires_manual_review`
File: `test2_mismatch.txt` · Attachment: `aadhaar_johndoe.png` (same image as test 1)

```
Name: Rahul Sharma
Email: rahul.sharma@example.com
Phone: 9123456780
Address: 12 MG Road, Bengaluru 560001

Attaching my identity document for verification.
```
Fails `name_consistency` (email *Rahul* vs document *John Doe*) **and** `duplicate_check`
(same document hash as test 1) → **requires_manual_review**.

## 3 — Casual prose, no attachment → `rejected`
File: `test3_prose_nodoc.txt`

```
Hi team,

I'm Priya Verma and I'd like to get onboarded. You can reach me at
priya.verma@example.com or on +91 98200 11223. I currently stay at
21 Residency Road, Bengaluru, Karnataka 560025.

Thanks,
Priya
```
Exercises spaCy NER (name/address) and `phonenumbers` (`+91 98200 11223` → `9820011223`).
No identity document → **rejected** (documents missing); notification `documents_missing`.

## 4 — Reply threading
Files: `test4a_original.txt`, then `test4b_reply.txt`

Original:
```
Message-ID: <onboard-arjun-001@example.com>
From: Arjun Nair <arjun.nair@example.com>

Hi, I'd like to begin onboarding. You can reach me on 9988776655.
```
Reply:
```
In-Reply-To: <onboard-arjun-001@example.com>
From: Arjun Nair <arjun.nair@example.com>

Quick follow-up on my onboarding request above - thanks!
```
The reply maps onto the **original** ticket (`"Reply mapped to existing ticket TKT-…"`)
instead of creating a new one. Re-sending any identical email returns `"idempotent": true`.

---

## Inspecting results

```bash
docker compose logs -f worker                                  # pipeline + console emails
curl -s http://localhost:8000/api/tickets | python -m json.tool
curl -s http://localhost:8000/api/tickets/<id>/timeline | python -m json.tool
```
