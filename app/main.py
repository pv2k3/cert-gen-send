from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.routers import projects, data, template, render, email, settings

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Certificate Generator")

# Mount static files for the frontend
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Serve the frontend index.html at the root
@app.get("/")
async def read_index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

# Project Management API
app.include_router(projects.router, prefix="/api/projects", tags=["projects"])

# Data API
app.include_router(data.router, prefix="/api/p", tags=["data"])

# Template API
app.include_router(template.router, prefix="/api/p", tags=["template"])

# Render API
app.include_router(render.router, prefix="/api/p", tags=["render"])

# Email API
app.include_router(email.router, prefix="/api/p", tags=["email"])

# Mail provider settings API
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])

@app.get("/api/health")
async def health_check():
    return {"status": "ok"}
