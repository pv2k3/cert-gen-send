import json
import os
import re
import threading
import time
from typing import Dict, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from PIL import Image

from app import config
from app.db import get_conn
from app.deps import ProjectId
from app.services import providers
from app.services.mailer import MissingPlaceholderError, substitute
from app.services.render import export_to_pdf

router = APIRouter()

EMAIL_RE = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
ATTACH_FORMATS = ("pdf", "png", "both")

# Projects with a send job currently running.
_SENDING: set = set()
_SEND_LOCK = threading.Lock()
# Provider switches / stops reported by the last job of each project, shown on the Send page.
_NOTES: Dict[str, list] = {}


def validate_email(email: str) -> bool:
    return bool(EMAIL_RE.match(email or ""))


def _ensure_account_column(conn):
    """Projects created before multi-account support lack email_settings.account."""
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(email_settings)")]
    if cols and "account" not in cols:
        conn.execute("ALTER TABLE email_settings ADD COLUMN account TEXT")
        conn.commit()


def _settings(conn) -> Optional[Dict]:
    _ensure_account_column(conn)
    row = conn.execute("SELECT * FROM email_settings WHERE id = 1").fetchone()
    return dict(row) if row else None


def _provider_for(s: Optional[Dict]) -> Optional[Dict]:
    """The mail provider this project sends with, or None when none is configured."""
    return providers.resolve((s or {}).get("account") or "")


def _providers_info(selected: str):
    return {
        "accounts": [{"id": p["id"], "name": p["name"], "type": p["type"], "from_email": p["from_email"]}
                     for p in providers.load() if providers.is_ready(p)],
        "account": selected,
    }


def _set_account(id: str, account: str):
    """Persists the chosen provider on an already-saved settings row."""
    if not any(p["id"] == account and providers.is_ready(p) for p in providers.load()):
        raise HTTPException(status_code=400, detail="Unknown mail provider")
    conn = get_conn(id)
    try:
        _ensure_account_column(conn)
        conn.execute("UPDATE email_settings SET account = ? WHERE id = 1", (account,))
        conn.commit()
    finally:
        conn.close()


def _one_line(text: str) -> str:
    return " ".join(text.split())  # subjects must not contain line breaks


def _build_attachments(rid: int, file_path: str, attach_format: str):
    attachments = []
    if attach_format in ("png", "both"):
        with open(file_path, "rb") as fh:
            attachments.append((f"certificate_{rid}.png", fh.read()))
    if attach_format in ("pdf", "both"):
        pdf_path = os.path.splitext(file_path)[0] + ".pdf"
        with Image.open(file_path) as img:
            export_to_pdf(img, pdf_path)
        with open(pdf_path, "rb") as fh:
            attachments.append((f"certificate_{rid}.pdf", fh.read()))
    return attachments


def send_emails_task(id: str):
    """Delivers every approved, not-yet-sent certificate. Safe to re-run: sent rows are never re-sent."""
    conn = get_conn(id)
    try:
        s = _settings(conn)
        if not s:
            return
        chain = providers.build_chain(s.get("account") or "")
        if not chain:
            return
        mailer = providers.FailoverSender(chain)
        _NOTES[id] = mailer.notes
        attach_format = s["attach_format"] if s["attach_format"] in ATTACH_FORMATS else "pdf"

        candidates = conn.execute(
            "SELECT c.recipient_id, c.file_path, r.data FROM certificates c "
            "JOIN recipients r ON c.recipient_id = r.id "
            "WHERE c.status = 'approved' "
            "AND c.recipient_id NOT IN (SELECT recipient_id FROM sends WHERE status = 'sent') "
            "ORDER BY c.id"
        ).fetchall()

        def record(rid, status, error=None, resend_id=None):
            conn.execute(
                "INSERT OR REPLACE INTO sends (recipient_id, status, error, resend_id, sent_at) "
                "VALUES (?, ?, ?, ?, CASE WHEN ? = 'sent' THEN datetime('now') END)",
                (rid, status, error, resend_id, status),
            )
            conn.commit()

        for cand in candidates:
            rid = cand["recipient_id"]
            data = json.loads(cand["data"])
            addr = (data.get(s["email_column"] or "", "") or "").strip()

            if not validate_email(addr):
                record(rid, "skipped", "Invalid or missing email address")
                continue

            try:
                if not cand["file_path"] or not os.path.exists(cand["file_path"]):
                    raise FileNotFoundError("Certificate file is missing; regenerate certificates")
                msg = {
                    "from_name": s["from_name"],
                    "to": [addr],
                    "subject": _one_line(substitute(s["subject"] or "", data, is_html=False)),
                    "html": substitute(s["html"] or "", data, is_html=True),
                    "attachments": _build_attachments(rid, cand["file_path"], attach_format),
                    "reply_to": s["reply_to"],
                    "idempotency_key": f"cert-{id}-{rid}",
                }
                record(rid, "sent", resend_id=mailer.send(msg))
            except providers.ProviderExhausted:
                # Nothing is recorded for this recipient, so it is still pending. Stop here so the
                # user can switch provider and continue instead of failing every remaining recipient.
                mailer.notes.append("Sending stopped: no mail provider left to send with. Fix or switch the provider, then continue.")
                break
            except MissingPlaceholderError as e:
                record(rid, "failed", f"Template error: {e}")
            except Exception as e:
                record(rid, "failed", str(e))

            time.sleep(config.SEND_DELAY_SECONDS)
        mailer.close()
    finally:
        conn.close()
        with _SEND_LOCK:
            _SENDING.discard(id)


def _start(id: str, background_tasks: BackgroundTasks):
    with _SEND_LOCK:
        if id in _SENDING:
            raise HTTPException(status_code=409, detail="Sending is already in progress")
        _SENDING.add(id)
    background_tasks.add_task(send_emails_task, id)


def _check_config(id: str):
    conn = get_conn(id)
    try:
        s = _settings(conn)
    finally:
        conn.close()
    if not _provider_for(s):
        raise HTTPException(status_code=400, detail="No mail provider is configured. Add one on the Settings page.")


# --- SETTINGS & PREVIEW ---

@router.get("/{id}/email")
async def get_email_settings(id: ProjectId):
    conn = get_conn(id)
    try:
        s = _settings(conn) or {}
        prov = _provider_for(s)
        return {
            **_providers_info((prov or {}).get("id", "")),
            "subject": s.get("subject") or "",
            "html": s.get("html") or "",
            "email_column": s.get("email_column") or "",
            "attach_format": s.get("attach_format") or "pdf",
            "from_name": s.get("from_name") or "",
            "reply_to": s.get("reply_to") or "",
            "resend_configured": bool(prov),
            "from_email": (prov or {}).get("from_email", ""),
        }
    finally:
        conn.close()


@router.put("/{id}/email")
async def save_email_settings(id: ProjectId, payload: dict):
    attach = payload.get("attach_format") or "pdf"
    if attach not in ATTACH_FORMATS:
        raise HTTPException(status_code=400, detail="attach_format must be pdf, png or both")
    if not (payload.get("email_column") or "").strip():
        raise HTTPException(status_code=400, detail="Choose the column that holds the email address")
    if not (payload.get("subject") or "").strip():
        raise HTTPException(status_code=400, detail="Subject is required")

    conn = get_conn(id)
    try:
        prior = _settings(conn) or {}
        known = {p["id"] for p in providers.load()}
        account = (payload.get("account") or "").strip()
        if account and account not in known:
            raise HTTPException(status_code=400, detail="Unknown mail provider")
        if not account and prior.get("account") in known:  # a deleted provider is simply forgotten
            account = prior["account"]
        conn.execute(
            "INSERT OR REPLACE INTO email_settings (id, subject, html, email_column, attach_format, from_name, reply_to, account) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
            (
                payload["subject"], payload.get("html") or "", payload["email_column"], attach,
                (payload.get("from_name") or "").strip(), (payload.get("reply_to") or "").strip(), account,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return {"status": "saved"}


@router.post("/{id}/email/preview")
async def preview_email(id: ProjectId, payload: dict):
    """Renders subject/body for one recipient. Template problems come back as `error`, not an HTTP error."""
    conn = get_conn(id)
    try:
        row = None
        if payload.get("recipient_id"):
            row = conn.execute("SELECT data FROM recipients WHERE id = ?", (payload["recipient_id"],)).fetchone()
        if not row:
            row = conn.execute("SELECT data FROM recipients ORDER BY id LIMIT 1").fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="No recipients found")
    finally:
        conn.close()

    data = json.loads(row["data"])
    try:
        return {
            "subject": _one_line(substitute(payload.get("subject") or "", data, is_html=False)),
            "html": substitute(payload.get("html") or "", data, is_html=True),
            "error": None,
        }
    except MissingPlaceholderError as e:
        return {"subject": "", "html": "", "error": str(e)}


# --- PLAN & SEND ---

@router.get("/{id}/send/plan")
async def get_send_plan(id: ProjectId):
    conn = get_conn(id)
    try:
        s = _settings(conn)
        if not s:
            raise HTTPException(status_code=400, detail="Email settings not configured")

        approved_count = conn.execute("SELECT count(*) FROM certificates WHERE status = 'approved'").fetchone()[0]
        rows = conn.execute(
            "SELECT c.recipient_id, r.data FROM certificates c JOIN recipients r ON c.recipient_id = r.id "
            "WHERE c.status = 'approved'"
        ).fetchall()
        already_sent = {r["recipient_id"] for r in conn.execute("SELECT recipient_id FROM sends WHERE status = 'sent'")}
    finally:
        conn.close()

    invalid, seen, to_send = [], {}, 0
    template_error = None
    for r in rows:
        data = json.loads(r["data"])
        if template_error is None:
            try:
                substitute(s["subject"] or "", data, is_html=False)
                substitute(s["html"] or "", data, is_html=True)
            except MissingPlaceholderError as e:
                template_error = str(e)

        addr = (data.get(s["email_column"] or "", "") or "").strip()
        if not validate_email(addr):
            invalid.append(data)
            continue
        seen[addr] = seen.get(addr, 0) + 1
        if r["recipient_id"] not in already_sent:
            to_send += 1

    return {
        "approved_count": approved_count,
        "valid_count": len(seen),
        "to_send": to_send,
        "already_sent": len([r for r in rows if r["recipient_id"] in already_sent]),
        "invalid_recipients": invalid,
        "duplicates": [e for e, n in seen.items() if n > 1],
        "template_error": template_error,
        "attachment_format": s["attach_format"],
        "subject": s["subject"],
        "email_column": s["email_column"],
        "resend_configured": bool(_provider_for(s)),
        **_providers_info((_provider_for(s) or {}).get("id", "")),
        "estimated_duration_sec": round(to_send * (config.SEND_DELAY_SECONDS + 0.3), 1),
    }


@router.post("/{id}/send")
async def start_sending(id: ProjectId, payload: dict, background_tasks: BackgroundTasks):
    if not payload.get("confirm"):
        raise HTTPException(status_code=400, detail="Confirmation is required to send emails.")
    conn = get_conn(id)
    try:
        if not _settings(conn):
            raise HTTPException(status_code=400, detail="Email settings not configured")
    finally:
        conn.close()
    if payload.get("account"):
        _set_account(id, payload["account"])
    _check_config(id)
    _start(id, background_tasks)
    return {"message": "Email delivery started in background"}


@router.get("/{id}/send/status")
async def get_send_status(id: ProjectId):
    conn = get_conn(id)
    try:
        rows = conn.execute(
            "SELECT s.recipient_id, s.status, s.error, s.sent_at, r.data FROM sends s "
            "JOIN recipients r ON s.recipient_id = r.id ORDER BY s.rowid DESC"
        ).fetchall()
        remaining = conn.execute(
            "SELECT count(*) FROM certificates WHERE status = 'approved' "
            "AND recipient_id NOT IN (SELECT recipient_id FROM sends)"
        ).fetchone()[0]
        s = _settings(conn)
    finally:
        conn.close()

    email_col = (s or {}).get("email_column") or ""
    results = []
    for r in rows:
        d = dict(r)
        d["email"] = json.loads(d.pop("data")).get(email_col, "")
        results.append(d)

    count = lambda st: len([r for r in rows if r["status"] == st])
    return {
        "running": id in _SENDING,
        "notes": list(_NOTES.get(id, [])),
        "remaining": remaining,
        "results": results,
        "summary": {"sent": count("sent"), "failed": count("failed"), "skipped": count("skipped")},
    }


@router.post("/{id}/send/retry-failed")
async def retry_failed(id: ProjectId, background_tasks: BackgroundTasks, payload: Optional[dict] = None):
    """Re-queues failed sends: all of them, or just {"recipient_id": n}. Optional {"account": id} switches provider first."""
    if (payload or {}).get("account"):
        _set_account(id, payload["account"])
    _check_config(id)
    conn = get_conn(id)
    try:
        # Clearing failed rows makes them candidates again; sent rows are untouched.
        rid = (payload or {}).get("recipient_id")
        if rid:
            n = conn.execute("DELETE FROM sends WHERE status = 'failed' AND recipient_id = ?", (rid,)).rowcount
        else:
            n = conn.execute("DELETE FROM sends WHERE status = 'failed'").rowcount
        conn.commit()
    finally:
        conn.close()
    if n:
        _start(id, background_tasks)
    return {"status": "retrying", "count": n}
