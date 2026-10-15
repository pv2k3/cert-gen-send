import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines); real environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")

# Put your Resend credentials in .env (see .env.example) or the environment.
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "")  # must be on a domain verified in Resend


def load_accounts() -> dict:
    """Resend accounts the user can switch between: {label: {"key", "from_email"}}.

    RESEND_API_KEYS="work=re_aaa,personal=re_bbb"; a label's sender is FROM_EMAIL_<LABEL>
    (falling back to FROM_EMAIL). The single RESEND_API_KEY is kept as the "default" account.
    """
    accounts = {}
    if RESEND_API_KEY:
        accounts["default"] = {"key": RESEND_API_KEY, "from_email": FROM_EMAIL}
    for part in os.getenv("RESEND_API_KEYS", "").split(","):
        label, _, key = part.partition("=")
        label, key = label.strip(), key.strip()
        if not label or not key:
            continue
        sender = os.getenv(f"FROM_EMAIL_{label.upper()}", "") or FROM_EMAIL
        accounts[label] = {"key": key, "from_email": sender}
    return accounts


def resolve_account(label: str = ""):
    """(label, account) for a saved label; falls back to the first account. (None, None) if none."""
    accounts = load_accounts()
    if label in accounts:
        return label, accounts[label]
    for first, acc in accounts.items():
        return first, acc
    return None, None

DATA_DIR = os.getenv("DATA_DIR", str(BASE_DIR / "data"))
SEND_DELAY_SECONDS = float(os.getenv("SEND_DELAY_SECONDS", "0.6"))
