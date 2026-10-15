# Certificate Generator

Local web app: upload an Excel sheet, place fields on a certificate image, review the generated
certificates, and email the approved ones through [Resend](https://resend.com). One project per event,
each with its own SQLite database under `data/<project>/`.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env      # optional: seed Resend key + FROM_EMAIL; mail providers are managed in the app's Settings page
.venv\Scripts\python run.py # http://127.0.0.1:8000
```

`FROM_EMAIL` must be an address on a domain you have verified in Resend. Without these two values the
whole app works except the final send step, which is blocked with a clear message.

## Workflow

1. **Data** - upload `.xlsx` (row 1 = headers), choose the columns to use.
2. **Template** - upload the certificate PNG/JPG.
3. **Design** - click the image to place each field, drag markers to fine-tune; set font, size, colour,
   bold, alignment and optional wrap width. The preview is rendered by the server, so it matches the output.
4. **Preview** - first/middle/last samples with real values, then generate all certificates.
5. **Review** - thumbnails, search/filter, bulk approve/reject. Only approved certificates are sent.
6. **Email** - subject and pasted HTML body with `{{Column Name}}` placeholders (case-insensitive), live preview,
   attachment as PDF, PNG or both.
7. **Send** - plan (counts, invalid/duplicate addresses), explicit confirmation, live progress, retry failed.

Notes: re-uploading data or the template, or changing the design, clears generated certificates.
Sent emails are never re-sent (each uses a Resend idempotency key). An unknown `{{placeholder}}` blocks sending.

## Tests

```powershell
.venv\Scripts\python -m pytest
```
Tests run against a temporary data directory and never touch `./data`.
