# Manual test: email thread detection

Goal: prove that a **reply** to an onboarding email is mapped onto the **original
ticket** (via the `In-Reply-To` / `Message-ID` headers) instead of creating a new one.

Files used: `thread_original.txt`, `thread_reply.txt` (in this folder).

```
thread_original.txt          thread_reply.txt
---------------------        ---------------------------------------
Message-ID: <onboard-meera-100@example.com>   In-Reply-To: <onboard-meera-100@example.com>
From: Meera Iyer <...>                         From: Meera Iyer <...>
(body)                                         (body)
```
The reply's `In-Reply-To` points at the original's `Message-ID` — that's the link.

## Steps

**1. Send the original.** Note the returned `ticket_id`.
```bash
curl -s -X POST http://localhost:8000/api/email/inbound -F "body=<samples/thread_original.txt"
```
Expected:
```json
{"ticket_id": "TKT-XXXXXXXX", "status": "received", "idempotent": false,
 "message": "Onboarding ticket created; processing started."}
```

**2. Send the reply.**
```bash
curl -s -X POST http://localhost:8000/api/email/inbound -F "body=<samples/thread_reply.txt"
```
Expected — **same `ticket_id` as step 1**, and a mapping message:
```json
{"ticket_id": "TKT-XXXXXXXX", "status": "...", "idempotent": false,
 "message": "Reply mapped to existing ticket TKT-XXXXXXXX."}
```

**3. Verify no new ticket was created.** The count is the same before and after the reply:
```bash
curl -s http://localhost:8000/api/tickets | python -m json.tool   # check "count"
```

**4. Verify the link on the original's timeline.** (Use the numeric ticket id; find it in
`GET /api/tickets`.)
```bash
curl -s http://localhost:8000/api/tickets/<id>/timeline | python -m json.tool
```
The last event is:
```
status_updated :: Reply received and mapped to TKT-XXXXXXXX
```

## What proves it worked

| Signal | Pass condition |
|--------|----------------|
| Reply response `ticket_id` | equals the original's `ticket_id` |
| Reply response `message` | `Reply mapped to existing ticket TKT-…` |
| Ticket count | unchanged by the reply (no new ticket) |
| Original timeline | ends with `Reply received and mapped to TKT-…` |

## Notes
- Matching is on the **`Message-ID`**, not the subject. If you change the reply's
  `In-Reply-To` to an unknown id, it will be treated as a brand-new onboarding instead.
- Sending the *exact same* body twice is a different case — caught by idempotency
  (`"idempotent": true`), not threading.
- The original ticket's own `status` ends up `rejected` here only because this sample
  has no attached document; that's incidental to threading.
