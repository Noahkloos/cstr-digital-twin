"""
Configuratie via omgevingsvariabelen (of een .env-bestand, zie .env.example).

    CSTR_HOST          host om op te luisteren        (standaard 127.0.0.1)
    CSTR_PORT          poort                           (standaard 8000)
    CSTR_TWIN_API_KEY  API-sleutel voor POST /api/twin (leeg = geen beveiliging)
    CSTR_LOG_LEVEL     debug | info | warning          (standaard info)
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"


def _load_dotenv() -> None:
    """Minimale .env-lader (geen extra dependency nodig)."""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

HOST = os.getenv("CSTR_HOST", "127.0.0.1")
PORT = int(os.getenv("CSTR_PORT", "8000"))
TWIN_API_KEY = os.getenv("CSTR_TWIN_API_KEY", "").strip()
LOG_LEVEL = os.getenv("CSTR_LOG_LEVEL", "info").lower()
