import os
import shutil
from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from PIL import Image
from app.db import get_conn
from app.deps import ProjectId, project_dir, find_template
from app.services.fonts import get_system_fonts, scan_dir

router = APIRouter()

IMAGE_EXTS = (".png", ".jpg", ".jpeg")


@router.post("/{id}/template")
async def upload_template(id: ProjectId, file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in IMAGE_EXTS:
        raise HTTPException(status_code=400, detail="Invalid file extension. Please upload a PNG or JPG image.")

    # Remove any previous template so there is only ever one.
    old = find_template(id)
    if old:
        os.remove(old)

    file_path = os.path.join(project_dir(id), f"template{ext}")
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    conn = get_conn(id)
    try:
        # A new template invalidates all existing certificates.
        conn.execute("DELETE FROM certificates")
        conn.commit()
    finally:
        conn.close()

    return {"message": "Template uploaded successfully", "path": file_path}


@router.get("/{id}/template")
async def get_template(id: ProjectId):
    path = find_template(id)
    if not path:
        raise HTTPException(status_code=404, detail="Template not found")
    info = {"filename": os.path.basename(path)}
    try:
        with Image.open(path) as im:
            info["width"], info["height"] = im.size
    except Exception:
        pass  # not a decodable image; the UI will show the failure when it loads it
    return info


@router.get("/{id}/template/image")
async def get_template_image(id: ProjectId):
    path = find_template(id)
    if not path:
        raise HTTPException(status_code=404, detail="Template not found")
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


@router.get("/fonts")
async def list_system_fonts():
    return get_system_fonts()


@router.get("/{id}/fonts")
async def list_fonts(id: ProjectId):
    """System fonts plus fonts uploaded to this project."""
    fonts_dir = os.path.join(project_dir(id), "fonts")
    project_fonts = scan_dir(fonts_dir, "project") if os.path.isdir(fonts_dir) else []
    return project_fonts + get_system_fonts()


@router.post("/{id}/fonts")
async def upload_font(id: ProjectId, file: UploadFile = File(...)):
    name = os.path.basename(file.filename or "")
    if not name.lower().endswith((".ttf", ".otf")):
        raise HTTPException(status_code=400, detail="Invalid file extension. Please upload a .ttf or .otf font.")

    fonts_dir = os.path.join(project_dir(id), "fonts")
    os.makedirs(fonts_dir, exist_ok=True)
    file_path = os.path.join(fonts_dir, name)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    return {"message": "Font uploaded successfully", "path": file_path}
