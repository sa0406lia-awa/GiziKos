from __future__ import annotations

import compileall
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    print("[1/2] Memeriksa sintaks Python...")
    if not compileall.compile_dir(ROOT / "app", quiet=1):
        print("Gagal: ditemukan kesalahan sintaks.")
        return 1
    print("[2/2] Menjalankan pengujian otomatis...")
    return subprocess.call([sys.executable, "-m", "pytest", "-q"], cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
