import uuid
from typing import Dict, List

from fastapi import APIRouter, HTTPException

from app.services import providers

router = APIRouter()


@router.get("/mail")
async def get_mail_settings():
    return {"providers": [providers.public(p) for p in providers.load()]}


@router.put("/mail")
async def save_mail_settings(payload: Dict):
    """Replaces the whole provider list (order matters: it is the fallback order)."""
    incoming: List[Dict] = payload.get("providers")
    if not isinstance(incoming, list):
        raise HTTPException(status_code=400, detail="providers must be a list")
    try:
        cleaned = providers.sanitize(incoming, providers.load())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    providers.save(cleaned)
    return {"providers": [providers.public(p) for p in cleaned]}


@router.post("/mail/{provider_id}/test")
async def test_provider(provider_id: str, payload: Dict):
    """Sends one test email through exactly this provider (no fallback) and reports the outcome."""
    to = (payload.get("to") or "").strip()
    from app.routers.email import validate_email
    if not validate_email(to):
        raise HTTPException(status_code=400, detail="Enter a valid address to send the test to")
    provider = next((p for p in providers.load() if p["id"] == provider_id), None)
    if not provider or not providers.is_ready(provider):
        raise HTTPException(status_code=404, detail="Provider not found or incomplete")
    mailer = providers.FailoverSender([{**provider, "fallback_to_next": False}])
    try:
        mail_id = mailer.send({
            "from_name": "", "to": [to], "subject": "Certificate Generator test email",
            "html": "<p>This is a test email from Certificate Generator. Your mail settings work.</p>",
            "attachments": [], "reply_to": "", "idempotency_key": f"test-{provider_id}-{uuid.uuid4().hex}",
        })
    except Exception as e:
        return {"ok": False, "error": str(e) or e.__class__.__name__}
    finally:
        mailer.close()
    return {"ok": True, "id": mail_id, "notes": mailer.notes}
