from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("GIZIKOS_DB_PATH", BASE_DIR / "gizikos.db"))
BACKUP_DIR = BASE_DIR / "backups"


def verify_backup(backup_path: Path) -> bool:
    try:
        conn = sqlite3.connect(backup_path)
        cursor = conn.cursor()
        # Verify that we can query sqlite_master
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        cursor.fetchall()
        conn.close()
        return True
    except Exception:
        return False


def backup_database() -> bool:
    if not DB_PATH.exists():
        print(f"Gagal: Database tidak ditemukan di {DB_PATH.as_posix()}", file=sys.stderr)
        return False

    try:
        BACKUP_DIR.mkdir(exist_ok=True)
    except Exception as e:
        print(f"Gagal membuat folder backup: {e}", file=sys.stderr)
        return False

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = BACKUP_DIR / f"gizikos_backup_{timestamp}.db"

    try:
        shutil.copy2(DB_PATH, backup_file)
        
        # Verify that the backup is a valid SQLite DB
        if verify_backup(backup_file):
            print(f"Berhasil: Database dicadangkan ke {backup_file.as_posix()}")
            return True
        else:
            print("Gagal: File backup rusak atau tidak dapat dibuka sebagai SQLite.", file=sys.stderr)
            if backup_file.exists():
                os.remove(backup_file)
            return False
            
    except Exception as e:
        print(f"Gagal melakukan backup: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    success = backup_database()
    sys.exit(0 if success else 1)
