import os
import re
import shutil
from fastapi import APIRouter, HTTPException
from app.db import get_conn, init_schema
from app.config import DATA_DIR
from app.deps import ProjectId, find_template

router = APIRouter()


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "project"


@router.post("/")
async def create_project(payload: dict):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Project name is required")

    slug = slugify(name)
    project_path = os.path.join(DATA_DIR, slug)
    if os.path.exists(project_path):
        raise HTTPException(status_code=400, detail="A project with this name already exists")

    os.makedirs(project_path)
    try:
        conn = get_conn(slug)
        try:
            init_schema(conn)
            conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)", ("project_name", name))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        shutil.rmtree(project_path, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Failed to initialize project: {e}")

    return {"id": slug, "name": name}


@router.get("/")
async def list_projects():
    if not os.path.isdir(DATA_DIR):
        return []

    projects = []
    for slug in sorted(os.listdir(DATA_DIR)):
        if not os.path.isfile(os.path.join(DATA_DIR, slug, "project.db")):
            continue
        try:
            conn = get_conn(slug)
            try:
                row = conn.execute("SELECT value FROM meta WHERE key = 'project_name'").fetchone()
                cnt = conn.execute("SELECT count(*) AS c FROM recipients").fetchone()["c"]
            finally:
                conn.close()
            projects.append({"id": slug, "name": row["value"] if row else slug, "recipients": cnt})
        except Exception:
            continue
    return projects


@router.delete("/{id}")
async def delete_project(id: ProjectId):
    shutil.rmtree(os.path.join(DATA_DIR, id), ignore_errors=True)
    return {"status": "deleted"}


@router.get("/{id}/state")
async def project_state(id: ProjectId):
    """Summary used by the wizard to resume where the user left off."""
    conn = get_conn(id)
    try:
        def one(sql):
            return conn.execute(sql).fetchone()[0]

        meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
        settings = conn.execute("SELECT email_column FROM email_settings WHERE id = 1").fetchone()
        return {
            "name": meta.get("project_name", id),
            "step": meta.get("step", "data"),
            "recipients": one("SELECT count(*) FROM recipients"),
            "columns": [r["name"] for r in conn.execute("SELECT name FROM columns ORDER BY position")],
            "has_template": find_template(id) is not None,
            "fields": one("SELECT count(*) FROM fields"),
            "certificates": {
                s: one(f"SELECT count(*) FROM certificates WHERE status = '{s}'")
                for s in ("pending", "approved", "rejected")
            },
            "email_configured": bool(settings and settings["email_column"]),
            "sent": one("SELECT count(*) FROM sends WHERE status = 'sent'"),
        }
    finally:
        conn.close()


@router.put("/{id}/step")
async def save_step(id: ProjectId, payload: dict):
    step = str(payload.get("step", ""))[:32]
    conn = get_conn(id)
    try:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('step', ?)", (step,))
        conn.commit()
    finally:
        conn.close()
    return {"step": step}
