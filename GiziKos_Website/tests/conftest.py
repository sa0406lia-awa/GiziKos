from __future__ import annotations

import os
import tempfile
from pathlib import Path
import pytest

# Tentukan path database pengujian sebelum modul aplikasi di-import
test_db_fd, test_db_path = tempfile.mkstemp(suffix=".db")
os.close(test_db_fd)

# Simpan path agar config.py membacanya
os.environ["GIZIKOS_DB_PATH"] = test_db_path
os.environ["GIZIKOS_ENV"] = "testing"

from app.database import Base, engine, SessionLocal
from app.seed import seed_database


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    # 1. Buat skema tabel lengkap
    Base.metadata.create_all(bind=engine)
    # 2. Masukkan data seed awal
    with SessionLocal() as db:
        seed_database(db, force=True)
    
    yield
    
    # 3. Bersihkan koneksi dan hapus file database pengujian
    try:
        engine.dispose()
        if os.path.exists(test_db_path):
            os.remove(test_db_path)
    except Exception:
        pass
