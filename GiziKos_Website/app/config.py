from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = Path(os.getenv("GIZIKOS_DB_PATH", BASE_DIR / "gizikos.db"))
DATABASE_URL = f"sqlite:///{DB_PATH.as_posix()}"
SECRET_KEY = os.getenv("GIZIKOS_SECRET_KEY", "gizikos-local-development-secret-change-before-production")
ADMIN_PASSWORD = os.getenv("GIZIKOS_ADMIN_PASSWORD", "gizikos-admin")
APP_NAME = "GiziKos"
APP_VERSION = "3.2.3"

GIZIKOS_ENV = os.getenv("GIZIKOS_ENV", "development").lower()

raw_debug_payload = os.getenv("GIZIKOS_ENABLE_DEBUG_PAYLOAD", "false").strip().lower()
ENABLE_DEBUG_PAYLOAD = raw_debug_payload in ("true", "1", "yes", "on")

if GIZIKOS_ENV == "production":
    if not SECRET_KEY or SECRET_KEY.strip() == "":
        raise ValueError("CRITICAL: GIZIKOS_SECRET_KEY kosong dalam mode produksi!")
    if len(SECRET_KEY) < 16:
        raise ValueError("CRITICAL: GIZIKOS_SECRET_KEY terlalu pendek (minimal 16 karakter)!")
    if SECRET_KEY in (
        "gizikos-local-development-secret-change-before-production",
        "change-me-in-production",
        "replace-with-a-long-random-secret",
        "ganti-dengan-random-secret-yang-panjang"
    ):
        raise ValueError("CRITICAL: GIZIKOS_SECRET_KEY masih menggunakan nilai contoh/demo!")

    if not ADMIN_PASSWORD or ADMIN_PASSWORD.strip() == "":
        raise ValueError("CRITICAL: GIZIKOS_ADMIN_PASSWORD kosong dalam mode produksi!")
    if len(ADMIN_PASSWORD) < 8:
        raise ValueError("CRITICAL: GIZIKOS_ADMIN_PASSWORD terlalu pendek (minimal 8 karakter)!")
    if ADMIN_PASSWORD in (
        "gizikos-admin",
        "replace-with-a-strong-password",
        "ganti-password-admin"
    ):
        raise ValueError("CRITICAL: GIZIKOS_ADMIN_PASSWORD masih menggunakan nilai demo/contoh!")
else:
    import sys
    if not SECRET_KEY or SECRET_KEY == "gizikos-local-development-secret-change-before-production":
        print("WARNING: Menggunakan SECRET_KEY default/kosong untuk pengembangan lokal. Ganti di produksi!", file=sys.stderr)
    if not ADMIN_PASSWORD or ADMIN_PASSWORD == "gizikos-admin":
        print("WARNING: Menggunakan ADMIN_PASSWORD default/kosong untuk pengembangan lokal. Ganti di produksi!", file=sys.stderr)

COMBINATION_WEIGHTS = {
    "fuzzy": 0.25,
    "energy_adequacy": 0.20,
    "protein_adequacy": 0.15,
    "budget": 0.10,
    "stock": 0.10,
    "goal": 0.08,
    "diversity": 0.12,
}
