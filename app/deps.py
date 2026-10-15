import os
from typing import Annotated
from fastapi import Depends, HTTPException
from app.config import DATA_DIR
from app.db import validate_project_id


def _existing_project(id: str) -> str:
    """Validates the id and that the project exists, before any route touches disk."""
    try:
        validate_project_id(id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not os.path.isfile(os.path.join(DATA_DIR, id, "project.db")):
        raise HTTPException(status_code=404, detail="Project not found")
    return id


ProjectId = Annotated[str, Depends(_existing_project)]


def project_dir(id: str) -> str:
    return os.path.join(DATA_DIR, id)


def find_template(id: str):
    d = project_dir(id)
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.startswith("template.") and f.lower().endswith((".png", ".jpg", ".jpeg")):
                return os.path.join(d, f)
    return None
