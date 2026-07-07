from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Set GIZIKOS_ENV to development before importing to allow fallback keys
os.environ.setdefault("GIZIKOS_ENV", "development")

from app.database import Base, engine, SessionLocal
from app.seed import seed_database

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("GIZIKOS_DB_PATH", BASE_DIR / "gizikos.db"))
BACKUP_DIR = BASE_DIR / "backups"


def verify_backup(backup_path: Path) -> bool:
    try:
        conn = sqlite3.connect(backup_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        cursor.fetchall()
        conn.close()
        return True
    except Exception:
        return False


def create_clean_database() -> bool:
    if DB_PATH.exists():
        try:
            BACKUP_DIR.mkdir(exist_ok=True)
        except Exception as e:
            print(f"Gagal: Tidak dapat membuat folder backup: {e}", file=sys.stderr)
            return False

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = BACKUP_DIR / f"gizikos_before_clean_{timestamp}.db"
        
        # 1. Buat backup sebelum penghapusan
        try:
            shutil.copy2(DB_PATH, backup_file)
        except Exception as e:
            print(f"Gagal: Backup sebelum pembersihan gagal: {e}", file=sys.stderr)
            return False

        # 2. Verifikasi backup
        if not verify_backup(backup_file):
            print("Gagal: Verifikasi backup gagal. Database aktif dipertahankan.", file=sys.stderr)
            if backup_file.exists():
                os.remove(backup_file)
            return False

        print(f"Informasi: Database lama berhasil dicadangkan dan diverifikasi di {backup_file.as_posix()}")

        # 3. Hapus database lama setelah backup terverifikasi
        try:
            engine.dispose()
            os.remove(DB_PATH)
            print(f"Informasi: File database lama di {DB_PATH.as_posix()} telah dihapus.")
        except Exception as e:
            print(f"Gagal: Gagal menghapus database lama: {e}", file=sys.stderr)
            return False

    print("Membuat tabel baru...")
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as e:
        print(f"Gagal membuat tabel baru: {e}", file=sys.stderr)
        return False

    print("Melakukan seeding data bawaan...")
    try:
        with SessionLocal() as db:
            seed_database(db, force=True)
        print("Berhasil: Database bersih siap digunakan.")
        return True
    except Exception as e:
        print(f"Gagal melakukan seeding data: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    success = create_clean_database()
    sys.exit(0 if success else 1)
