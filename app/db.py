import sqlite3
import os
import re
from app.config import DATA_DIR

def validate_project_id(project_id: str):
    # Slug regex: letters, numbers, dashes, underscores. No dots or slashes.
    if not re.match(r"^[a-zA-Z0-9_-]+$", project_id):
        raise ValueError(f"Invalid project ID: {project_id}. Use only alphanumeric, underscores, and dashes.")
    return project_id

def get_conn(project_id: str) -> sqlite3.Connection:
    pid = validate_project_id(project_id)
    db_path = os.path.join(DATA_DIR, pid, "project.db")

    # Ensure directory exists
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Concurrency settings
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")

    return conn

def init_schema(conn: sqlite3.Connection):
    # meta: project settings and wizard state
    conn.execute("""
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)

    # recipients: raw data from excel
    conn.execute("""
    CREATE TABLE IF NOT EXISTS recipients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        row_index INTEGER,
        data JSON
    )
    """)

    # columns: selected fields for the cert
    conn.execute("""
    CREATE TABLE IF NOT EXISTS columns (
        name TEXT PRIMARY KEY,
        selected BOOLEAN,
        position INTEGER
    )
    """)

    # fields: visual placement and typography
    conn.execute("""
    CREATE TABLE IF NOT EXISTS fields (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        column_name TEXT,
        x INTEGER,
        y INTEGER,
        font_family TEXT,
        font_file TEXT,
        size INTEGER,
        color TEXT,
        bold BOOLEAN,
        align TEXT,
        max_width INTEGER
    )
    """)

    # certificates: rendered files and approvals
    conn.execute("""
    CREATE TABLE IF NOT EXISTS certificates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recipient_id INTEGER,
        file_path TEXT,
        thumbnail_path TEXT,
        status TEXT
    )
    """)

    # email_settings: global configuration for mailing
    conn.execute("""
    CREATE TABLE IF NOT EXISTS email_settings (
        id INTEGER PRIMARY KEY CHECK (id=1),
        subject TEXT,
        html TEXT,
        email_column TEXT,
        attach_format TEXT,
        from_name TEXT,
        reply_to TEXT
    )
    """)

    # sends: audit trail for Resend delivery
    conn.execute("""
    CREATE TABLE IF NOT EXISTS sends (
        recipient_id INTEGER PRIMARY KEY,
        status TEXT,
        error TEXT,
        resend_id TEXT,
        sent_at DATETIME
    )
    """)

    # migration: which Resend account (label from .env) this project sends with
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(email_settings)")]
    if "account" not in cols:
        conn.execute("ALTER TABLE email_settings ADD COLUMN account TEXT")

    conn.commit()
