import base64
import json
import os
import threading
from io import BytesIO
from typing import Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel

from app.config import DATA_DIR
from app.db import get_conn
from app.deps import ProjectId, find_template
from app.services.render import render_certificate

router = APIRouter()

# In-memory state of background generation jobs, keyed by project id.
_GEN: Dict[str, Dict] = {}
_GEN_LOCK = threading.Lock()


class FieldUpdate(BaseModel):
    column_name: str
    x: int
    y: int
    font_family: Optional[str] = None
    font_file: Optional[str] = None
    size: Optional[int] = None
    color: Optional[str] = None
    bold: Optional[bool] = None
    align: Optional[str] = None
    max_width: Optional[int] = None


# --- FIELD MANAGEMENT ---

@router.get("/{id}/fields")
async def get_fields(id: ProjectId):
    conn = get_conn(id)
    try:
        return [dict(f) for f in conn.execute("SELECT * FROM fields ORDER BY id")]
    finally:
        conn.close()


@router.put("/{id}/fields")
async def update_fields(id: ProjectId, payload: List[FieldUpdate]):
    conn = get_conn(id)
    try:
        conn.execute("DELETE FROM fields")
        conn.executemany(
            """INSERT INTO fields
            (column_name, x, y, font_family, font_file, size, color, bold, align, max_width)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(f.column_name, f.x, f.y, f.font_family, f.font_file, f.size, f.color, f.bold, f.align, f.max_width)
             for f in payload],
        )
        # Changed fields make already-rendered certificates stale.
        conn.execute("DELETE FROM certificates")
        conn.commit()
    finally:
        conn.close()
    return {"status": "fields updated"}


# --- GENERATION ---

def generate_certs_task(id: str):
    state = _GEN[id]
    conn = get_conn(id)
    try:
        template_file = find_template(id)
        fields = [dict(f) for f in conn.execute("SELECT * FROM fields")]
        recipients = conn.execute("SELECT id, data FROM recipients ORDER BY id").fetchall()

        certs_dir = os.path.join(DATA_DIR, id, "certs")
        thumbs_dir = os.path.join(certs_dir, "thumbs")
        os.makedirs(thumbs_dir, exist_ok=True)

        for recip in recipients:
            rid = recip["id"]
            img = render_certificate(template_file, fields, json.loads(recip["data"]))
            file_path = os.path.join(certs_dir, f"cert_{rid}.png")
            img.save(file_path, "PNG")

            thumb = img.resize((720, max(1, int(720 * img.height / img.width))), Image.Resampling.LANCZOS)
            thumb_path = os.path.join(thumbs_dir, f"thumb_{rid}.png")
            thumb.save(thumb_path, "PNG")

            conn.execute(
                "INSERT INTO certificates (recipient_id, file_path, thumbnail_path, status) VALUES (?, ?, ?, 'pending')",
                (rid, file_path, thumb_path),
            )
            conn.commit()
    except Exception as e:  # surfaced to the UI through /generate/status
        state["error"] = str(e)
    finally:
        state["running"] = False
        conn.close()


@router.post("/{id}/generate")
async def start_generation(id: ProjectId, background_tasks: BackgroundTasks):
    conn = get_conn(id)
    try:
        if not find_template(id):
            raise HTTPException(status_code=400, detail="Upload a certificate template first")
        if conn.execute("SELECT count(*) FROM fields").fetchone()[0] == 0:
            raise HTTPException(status_code=400, detail="Place at least one field first")
        if conn.execute("SELECT count(*) FROM recipients").fetchone()[0] == 0:
            raise HTTPException(status_code=400, detail="Upload an Excel file first")

        with _GEN_LOCK:
            if _GEN.get(id, {}).get("running"):
                raise HTTPException(status_code=409, detail="Generation is already running")
            _GEN[id] = {"running": True, "error": None}

        # Regenerating starts from scratch (this also resets approvals).
        conn.execute("DELETE FROM certificates")
        conn.commit()
    finally:
        conn.close()

    background_tasks.add_task(generate_certs_task, id)
    return {"message": "Generation started in background"}


@router.get("/{id}/generate/status")
async def get_generation_status(id: ProjectId):
    conn = get_conn(id)
    try:
        total = conn.execute("SELECT count(*) FROM recipients").fetchone()[0]
        done = conn.execute("SELECT count(*) FROM certificates").fetchone()[0]
    finally:
        conn.close()

    state = _GEN.get(id, {})
    running = bool(state.get("running"))
    error = state.get("error")
    progress = 100 if total == 0 else int(done / total * 100)
    if error:
        status = "failed"
    elif running:
        status = "processing"
    else:
        status = "complete" if done else "idle"
    return {"status": status, "progress": progress, "total": total, "done": done, "error": error}


# --- PREVIEW ---

@router.post("/{id}/preview")
async def get_preview(id: ProjectId, payload: dict):
    """Renders sample certificates. Accepts recipient_id or recipient_ids, and optional unsaved `fields`."""
    ids = payload.get("recipient_ids") or ([payload["recipient_id"]] if payload.get("recipient_id") else [])

    conn = get_conn(id)
    try:
        rows = []
        for rid in ids:
            r = conn.execute("SELECT data FROM recipients WHERE id = ?", (rid,)).fetchone()
            if r:
                rows.append(r)
        if not rows:
            r = conn.execute("SELECT data FROM recipients ORDER BY id LIMIT 1").fetchone()
            if not r:
                raise HTTPException(status_code=404, detail="No recipients found")
            rows = [r]

        fields = payload["fields"] if "fields" in payload else [dict(f) for f in conn.execute("SELECT * FROM fields")]
    finally:
        conn.close()

    template_file = find_template(id)
    if not template_file:
        raise HTTPException(status_code=404, detail="Template not found")

    images = []
    for r in rows:
        try:
            img = render_certificate(template_file, fields, json.loads(r["data"]))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Could not render preview: {e}")
        buf = BytesIO()
        img.save(buf, format="PNG")
        images.append("data:image/png;base64," + base64.b64encode(buf.getvalue()).decode())

    return {"image": images[0], "images": images}


# --- REVIEW API ---

def _cert_filters(status: Optional[str], q: Optional[str]):
    where, params = "1=1", []
    if status:
        where += " AND c.status = ?"
        params.append(status)
    if q:
        where += " AND r.data LIKE ?"
        params.append(f"%{q}%")
    return where, params


@router.get("/{id}/certificates")
async def get_certificates(
    id: ProjectId,
    status: Optional[str] = None,
    q: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(48, ge=1, le=200),
):
    where, params = _cert_filters(status, q)
    conn = get_conn(id)
    try:
        rows = conn.execute(
            f"SELECT c.id, c.recipient_id, c.status, r.data FROM certificates c "
            f"JOIN recipients r ON c.recipient_id = r.id WHERE {where} ORDER BY c.id LIMIT ? OFFSET ?",
            params + [page_size, (page - 1) * page_size],
        ).fetchall()
        total = conn.execute(
            f"SELECT count(*) FROM certificates c JOIN recipients r ON c.recipient_id = r.id WHERE {where}", params
        ).fetchone()[0]
        counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, count(*) AS n FROM certificates GROUP BY status")}
        return {
            "certificates": [dict(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "counts": counts,
        }
    finally:
        conn.close()


def _cert_file(id: str, cid: int, column: str):
    conn = get_conn(id)
    try:
        row = conn.execute(f"SELECT {column} FROM certificates WHERE id = ?", (cid,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Certificate not found")
    path = row[column]
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Certificate file missing on disk")
    return FileResponse(path)


@router.get("/{id}/certificates/{cid}/image")
async def get_certificate_image(id: ProjectId, cid: int):
    return _cert_file(id, cid, "file_path")


@router.get("/{id}/certificates/{cid}/thumb")
async def get_certificate_thumb(id: ProjectId, cid: int):
    return _cert_file(id, cid, "thumbnail_path")


@router.patch("/{id}/certificates")
async def update_certificates_status(id: ProjectId, payload: dict):
    """Bulk approve/reject by `ids`, or every certificate matching `filter` when `all` is true."""
    status = payload.get("status")
    if status not in ("approved", "rejected", "pending"):
        raise HTTPException(status_code=400, detail="Invalid status")

    conn = get_conn(id)
    try:
        if payload.get("all"):
            flt = payload.get("filter") or {}
            where, params = _cert_filters(flt.get("status"), flt.get("q"))
            ids = [r["id"] for r in conn.execute(
                f"SELECT c.id FROM certificates c JOIN recipients r ON c.recipient_id = r.id WHERE {where}", params)]
        else:
            ids = payload.get("ids")
            if not ids or not isinstance(ids, list):
                raise HTTPException(status_code=400, detail="List of IDs is required")

        conn.executemany("UPDATE certificates SET status = ? WHERE id = ?", [(status, cid) for cid in ids])
        conn.commit()
    finally:
        conn.close()
    return {"status": "updated", "count": len(ids)}
