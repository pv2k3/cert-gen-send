import os
import json
import shutil
from fastapi import APIRouter, HTTPException, UploadFile, File
from app.db import get_conn
from app.deps import ProjectId, project_dir
from app.services.excel import parse_excel

router = APIRouter()


@router.post("/{id}/excel")
async def upload_excel(id: ProjectId, file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm", ".xltx", ".xltm")):
        raise HTTPException(status_code=400, detail="Invalid file extension. Please upload an .xlsx file.")

    file_path = os.path.join(project_dir(id), "data.xlsx")
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        rows, headers = parse_excel(file_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        raise HTTPException(status_code=400, detail="Could not read this file. Is it a valid .xlsx workbook?")

    if not headers:
        raise HTTPException(status_code=400, detail="The first sheet has no header row.")

    conn = get_conn(id)
    try:
        # New data invalidates everything derived from the old data.
        conn.execute("DELETE FROM recipients")
        conn.execute("DELETE FROM certificates")
        conn.execute("DELETE FROM sends")
        conn.execute("DELETE FROM columns")
        # Keep placed fields only if their column still exists.
        marks = ",".join("?" * len(headers))
        conn.execute(f"DELETE FROM fields WHERE column_name NOT IN ({marks})", headers)
        conn.executemany(
            "INSERT INTO recipients (row_index, data) VALUES (?, ?)",
            [(i, json.dumps(row)) for i, row in enumerate(rows, start=1)],
        )
        conn.commit()
    finally:
        conn.close()

    return {"message": "Excel uploaded and processed", "row_count": len(rows), "headers": headers}


@router.get("/{id}/data")
async def get_data(id: ProjectId, limit: int = 10):
    conn = get_conn(id)
    try:
        total = conn.execute("SELECT count(*) FROM recipients").fetchone()[0]
        rows = conn.execute("SELECT id, data FROM recipients ORDER BY id LIMIT ?", (min(max(limit, 1), 500),)).fetchall()
        preview = [json.loads(r["data"]) for r in rows]
        columns = list(preview[0].keys()) if preview else []
        ids = [r["id"] for r in conn.execute("SELECT id FROM recipients ORDER BY id")]
        sample_ids = sorted({ids[0], ids[len(ids) // 2], ids[-1]}) if ids else []
        selected = [r["name"] for r in conn.execute("SELECT name FROM columns ORDER BY position")]
        return {
            "preview": preview,
            "recipient_ids": [r["id"] for r in rows],
            "columns": columns,
            "selected": selected,
            "sample_ids": sample_ids,
            "total": total,
        }
    finally:
        conn.close()


@router.put("/{id}/columns")
async def update_columns(id: ProjectId, payload: dict):
    selected = payload.get("selected")
    if not isinstance(selected, list) or not selected:
        raise HTTPException(status_code=400, detail="Select at least one column")

    conn = get_conn(id)
    try:
        conn.execute("DELETE FROM columns")
        conn.executemany(
            "INSERT INTO columns (name, selected, position) VALUES (?, ?, ?)",
            [(name, True, i) for i, name in enumerate(selected)],
        )
        # Drop placed fields for columns that are no longer selected.
        marks = ",".join("?" * len(selected))
        conn.execute(f"DELETE FROM fields WHERE column_name NOT IN ({marks})", selected)
        conn.commit()
    finally:
        conn.close()

    return {"status": "columns updated"}
