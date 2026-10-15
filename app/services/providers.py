"""Mail providers (Resend with several API keys, SMTP) and the failover between them.

Providers live in DATA_DIR/mail_providers.json and are edited on the Settings page. On first
use the file is seeded from the RESEND_API_KEY / RESEND_API_KEYS variables in .env.
"""
import json
import os
import smtplib
import ssl
import threading
import time
import uuid
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Dict, List, Optional

import resend

from app import config

MAX_ATTEMPTS = 4  # per Resend key, on plain rate limiting
SECURITY_MODES = ("ssl", "starttls", "none")
_STORE_LOCK = threading.Lock()
_RESEND_LOCK = threading.Lock()  # resend.api_key is process-global, so Resend sends are serialised


class ProviderExhausted(Exception):
    """This provider can't send any more (limit reached, bad credentials, server refusing us)."""


# --- storage ---

def _path() -> str:
    return os.path.join(config.DATA_DIR, "mail_providers.json")


def _seed_from_env() -> List[Dict]:
    result: List[Dict] = []
    if os.getenv("SMTP_HOST"):
        user = os.getenv("SMTP_USER") or os.getenv("SMTP_USERNAME", "")
        result.append({
            "id": uuid.uuid4().hex[:8], "type": "smtp", "name": "SMTP", "host": os.getenv("SMTP_HOST"),
            "port": int(os.getenv("SMTP_PORT", "587")), "security": os.getenv("SMTP_SECURITY", "starttls"),
            "username": user, "password": os.getenv("SMTP_PASSWORD", "") if user else "",
            "from_email": os.getenv("SMTP_FROM") or os.getenv("SMTP_FROM_EMAIL") or os.getenv("FROM_EMAIL") or user,
            "from_name": os.getenv("SMTP_FROM_NAME", ""), "fallback_to_next": False,
        })
    return result


def load() -> List[Dict]:
    with _STORE_LOCK:
        try:
            with open(_path(), encoding="utf-8") as fh:
                return json.load(fh)["providers"]
        except FileNotFoundError:
            providers = _seed_from_env()
            if providers:
                _write(providers)
            return providers


def _write(providers: List[Dict]) -> None:
    os.makedirs(config.DATA_DIR, exist_ok=True)
    tmp = _path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"providers": providers}, fh, indent=2)
    os.replace(tmp, _path())


def save(providers: List[Dict]) -> None:
    with _STORE_LOCK:
        _write(providers)


def is_ready(p: Dict) -> bool:
    if not p.get("from_email"):
        return False
    if p["type"] != "smtp":  # mail is always sent over SMTP; Resend providers are ignored
        return False
    return bool(p.get("host"))


def resolve(provider_id: str = "") -> Optional[Dict]:
    """The saved provider, else the first usable one, else None."""
    providers = load()
    for p in providers:
        if p["id"] == provider_id and is_ready(p):
            return p
    return next((p for p in providers if is_ready(p)), None)


def _mask(key: str) -> str:
    return f"{key[:3]}…{key[-4:]}" if len(key) > 8 else "set"


def public(p: Dict) -> Dict:
    """Provider as shown to the browser: secrets replaced by a hint."""
    out = {k: v for k, v in p.items() if k not in ("keys", "password")}
    if p["type"] == "resend":
        out["keys"] = [{"id": k["id"], "label": k["label"], "hint": _mask(k["key"])} for k in p.get("keys", [])]
    else:
        out["has_password"] = bool(p.get("password"))
    out["ready"] = is_ready(p)
    return out


def sanitize(incoming: List[Dict], existing: List[Dict]) -> List[Dict]:
    """Validates the list posted by Settings. Blank secrets keep the stored value."""
    old = {p["id"]: p for p in existing}
    result, names = [], set()
    for raw in incoming:
        pid = raw.get("id") or uuid.uuid4().hex[:8]
        prev = old.get(pid, {})
        name = (raw.get("name") or "").strip()
        if not name:
            raise ValueError("Every provider needs a name")
        if name.lower() in names:
            raise ValueError(f"Two providers are called '{name}'")
        names.add(name.lower())
        from_email = (raw.get("from_email") or "").strip()
        if not from_email:
            raise ValueError(f"'{name}': enter the From address")
        p = {"id": pid, "type": raw.get("type"), "name": name, "from_email": from_email,
             "fallback_to_next": bool(raw.get("fallback_to_next"))}
        if p["type"] == "resend":
            old_keys = {k["id"]: k for k in prev.get("keys", [])}
            keys = []
            for k in raw.get("keys") or []:
                kid = k.get("id") or uuid.uuid4().hex[:8]
                secret = (k.get("key") or "").strip() or old_keys.get(kid, {}).get("key", "")
                if not secret:
                    raise ValueError(f"'{name}': an API key is empty")
                keys.append({"id": kid, "label": (k.get("label") or "").strip() or f"key {len(keys) + 1}", "key": secret})
            if not keys:
                raise ValueError(f"'{name}': add at least one API key")
            p["keys"] = keys
        elif p["type"] == "smtp":
            host = (raw.get("host") or "").strip()
            if not host:
                raise ValueError(f"'{name}': enter the SMTP host")
            try:
                port = int(raw.get("port"))
            except (TypeError, ValueError):
                raise ValueError(f"'{name}': the port must be a number")
            if raw.get("security") not in SECURITY_MODES:
                raise ValueError(f"'{name}': security must be ssl, starttls or none")
            username = (raw.get("username") or "").strip()
            p.update(host=host, port=port, security=raw["security"], username=username,
                     from_name=prev.get("from_name", ""),
                     password=((raw.get("password") or "") or prev.get("password", "")) if username else "")
        else:
            raise ValueError(f"'{name}': unknown provider type")
        result.append(p)
    return result


# --- senders ---
# A message is a dict: from_name, to (list), subject, html, reply_to, attachments [(filename, bytes)],
# idempotency_key. Each sender returns an id for the sent mail or raises ProviderExhausted / another error.

def _from_header(from_name: str, from_email: str) -> str:
    name = (from_name or "").strip()
    return from_email if "<" in from_email or not name else f"{name} <{from_email}>"


class ResendSender:
    def __init__(self, provider: Dict, notify):
        self.p, self.notify = provider, notify
        self.keys = list(provider["keys"])
        self.i = 0

    @property
    def name(self) -> str:
        return self.p["name"]

    def send(self, msg: Dict) -> str:
        while self.i < len(self.keys):
            key = self.keys[self.i]
            try:
                return self._send_with(key, msg)
            except resend.exceptions.RateLimitError as e:
                # daily/monthly quota can't recover during this job; plain rate limits already had their backoff
                reason = "reached its sending limit" if getattr(e, "error_type", "") != "rate_limit_exceeded" else "kept rate-limiting us"
            except (resend.exceptions.InvalidApiKeyError, resend.exceptions.MissingApiKeyError):
                reason = "was rejected as invalid"
            self.i += 1
            nxt = f"switching to '{self.keys[self.i]['label']}'" if self.i < len(self.keys) else "no keys left"
            self.notify(f"{self.name}: key '{key['label']}' {reason}, {nxt}.")
        raise ProviderExhausted(f"All Resend keys of '{self.name}' are exhausted")

    def _send_with(self, key: Dict, msg: Dict) -> str:
        params = {
            "from": _from_header(msg["from_name"], self.p["from_email"]),
            "to": msg["to"], "subject": msg["subject"], "html": msg["html"],
            "attachments": [{"filename": n, "content": list(b)} for n, b in msg["attachments"]],
        }
        if msg.get("reply_to"):
            params["reply_to"] = msg["reply_to"]
        # The key id is in the idempotency key: a retry on another key is a new send, not a replay.
        options = {"idempotency_key": f"{msg['idempotency_key']}-{key['id']}"}
        delay = 1.0
        for attempt in range(MAX_ATTEMPTS):
            try:
                with _RESEND_LOCK:
                    resend.api_key = key["key"]
                    return resend.Emails.send(params, options).get("id")
            except resend.exceptions.RateLimitError as e:
                if getattr(e, "error_type", "") != "rate_limit_exceeded" or attempt == MAX_ATTEMPTS - 1:
                    raise
                time.sleep(delay)
                delay *= 2

    def close(self):
        pass


class SMTPSender:
    def __init__(self, provider: Dict, notify):
        self.p, self.notify = provider, notify
        self.conn: Optional[smtplib.SMTP] = None

    @property
    def name(self) -> str:
        return self.p["name"]

    def _connect(self):
        p = self.p
        if p["security"] == "ssl":
            conn = smtplib.SMTP_SSL(p["host"], p["port"], timeout=30, context=ssl.create_default_context())
        else:
            conn = smtplib.SMTP(p["host"], p["port"], timeout=30)
            if p["security"] == "starttls":
                conn.starttls(context=ssl.create_default_context())
        if p.get("username"):
            conn.login(p["username"], p.get("password", ""))
        self.conn = conn

    def close(self):
        if self.conn:
            try:
                self.conn.quit()
            except Exception:
                pass
            self.conn = None

    def _build(self, msg: Dict) -> EmailMessage:
        m = EmailMessage()
        m["From"] = _from_header(msg["from_name"] or self.p.get("from_name", ""), self.p["from_email"])
        m["To"] = ", ".join(msg["to"])
        m["Subject"] = msg["subject"]
        m["Message-ID"] = make_msgid()
        if msg.get("reply_to"):
            m["Reply-To"] = msg["reply_to"]
        m.set_content("This email contains HTML. Please view it in an HTML-capable mail client.")
        m.add_alternative(msg["html"], subtype="html")
        for name, data in msg["attachments"]:
            maintype, subtype = ("application", "pdf") if name.endswith(".pdf") else ("image", "png")
            m.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
        return m

    def send(self, msg: Dict) -> str:
        m = self._build(msg)
        for attempt in (1, 2):
            try:
                if self.conn is None:
                    self._connect()
                self.conn.send_message(m)
                return m["Message-ID"]
            except smtplib.SMTPAuthenticationError as e:
                self.close()
                raise ProviderExhausted(f"'{self.name}': login rejected ({e.smtp_code})") from e
            except smtplib.SMTPRecipientsRefused:
                raise  # this address is bad; the server itself is fine
            except (smtplib.SMTPServerDisconnected, ConnectionError, TimeoutError, ssl.SSLError):
                self.close()  # stale connection: reconnect once
                if attempt == 2:
                    raise
            except smtplib.SMTPResponseException as e:
                if e.smtp_code in (421, 450, 451, 452) or b"limit" in e.smtp_error.lower() or b"quota" in e.smtp_error.lower():
                    self.close()
                    raise ProviderExhausted(f"'{self.name}': server refused to send ({e.smtp_code} {e.smtp_error.decode(errors='replace')})") from e
                raise
            except smtplib.SMTPException:
                raise
            except OSError as e:
                self.close()
                raise ProviderExhausted(f"'{self.name}': cannot reach {self.p['host']}:{self.p['port']} ({e})") from e


class FailoverSender:
    """Sends through the chosen provider; moves to the next one when it is exhausted (if allowed)."""

    def __init__(self, chain: List[Dict]):
        self.notes: List[str] = []
        self.senders = [(ResendSender if p["type"] == "resend" else SMTPSender)(p, self.notes.append) for p in chain]
        self.chain = chain
        self.i = 0

    @property
    def current(self) -> str:
        return self.senders[min(self.i, len(self.senders) - 1)].name

    def send(self, msg: Dict) -> str:
        while self.i < len(self.senders):
            try:
                return self.senders[self.i].send(msg)
            except ProviderExhausted as e:
                nxt = self.i + 1 < len(self.senders) and self.chain[self.i].get("fallback_to_next")
                self.notes.append(f"{e}." + (f" Switching to '{self.senders[self.i + 1].name}'." if nxt else " No fallback provider."))
                self.senders[self.i].close()
                if not nxt:
                    raise
                self.i += 1
        raise ProviderExhausted("No mail provider available")

    def close(self):
        for s in self.senders:
            s.close()


def build_chain(provider_id: str) -> List[Dict]:
    """The chosen provider followed by the providers after it, as far as 'fallback to next' links them."""
    providers = [p for p in load() if is_ready(p)]
    first = resolve(provider_id)
    if not first:
        return []
    chain = [first]
    idx = next(i for i, p in enumerate(providers) if p["id"] == first["id"])
    while chain[-1].get("fallback_to_next") and idx + 1 < len(providers):
        idx += 1
        chain.append(providers[idx])
    return chain
