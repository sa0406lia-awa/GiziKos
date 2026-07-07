from __future__ import annotations

import os
import shutil
import sys
import zipfile
import hashlib
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ZIP_NAME = "GiziKos_Website_Final_Production.zip"
CHECKSUM_NAME = f"{ZIP_NAME}.sha256"

# Exclusion directories/files
EXCLUDE_DIRS = {
    ".venv", "venv", "__pycache__", ".pytest_cache", ".git", ".idea", ".vscode",
    "dist", "build", "scratch", "backups", "build_staging", "htmlcov"
}

EXCLUDE_FILES = {
    "gizikos.db", "gizikos_backup.db", ".env", ".coverage", ZIP_NAME, CHECKSUM_NAME
}

EXCLUDE_EXTENSIONS = {
    ".pyc", ".pyo", ".pyd", ".sqlite", ".sqlite3", ".db"
}

# Essential files that MUST be present in the ZIP
REQUIRED_FILES = {
    "start.bat", "run.py", "requirements.txt", "pyproject.toml",
    "data/foods.csv", "data/recipes.json", "app/main.py"
}


@dataclass
class ReleaseResult:
    success: bool
    zip_path: Path | None = None
    checksum_path: Path | None = None
    error_message: str | None = None

    def __bool__(self) -> bool:
        return self.success


def clean_staging(staging_dir: Path) -> None:
    if staging_dir.exists():
        shutil.rmtree(staging_dir)


def should_exclude(path: Path, source_dir: Path) -> bool:
    try:
        rel_path = path.relative_to(source_dir)
    except ValueError:
        return True

    parts = rel_path.parts
    if not parts:
        return False

    for part in parts[:-1]:
        if part in EXCLUDE_DIRS:
            return True

    name = path.name
    if name in EXCLUDE_DIRS or name in EXCLUDE_FILES:
        return True

    if name.endswith(".sha256") or name.endswith(".zip"):
        if name == ZIP_NAME or name == CHECKSUM_NAME or name.startswith("GiziKos_Website_"):
            return True

    if path.is_file():
        if path.suffix.lower() in EXCLUDE_EXTENSIONS:
            return True
        if name.startswith("test_") and name.endswith(".db"):
            return True

    return False


def calculate_sha256(filepath: Path) -> str:
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def build_release(
    source_dir: Path = BASE_DIR,
    output_dir: Path = BASE_DIR,
    zip_name: str = ZIP_NAME,
) -> ReleaseResult:
    source_dir = Path(source_dir).resolve()
    output_dir = Path(output_dir).resolve()
    checksum_name = f"{zip_name}.sha256"

    staging_dir = output_dir / "build_staging"
    zip_path = output_dir / zip_name
    checksum_path = output_dir / checksum_name

    print("=" * 60)
    print(f"Membuka proses pembuatan rilis produksi untuk {source_dir}...")
    print("=" * 60)

    clean_staging(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    copied_count = 0
    for root, dirs, files in os.walk(source_dir):
        root_path = Path(root)

        # Skip staging_dir if inside source_dir
        if staging_dir in root_path.parents or root_path == staging_dir:
            continue

        dirs[:] = [d for d in dirs if not should_exclude(root_path / d, source_dir)]

        for file in files:
            file_path = root_path / file
            if should_exclude(file_path, source_dir):
                continue

            rel_path = file_path.relative_to(source_dir)
            dest_path = staging_dir / rel_path
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file_path, dest_path)
            copied_count += 1

    print(f"Berhasil menyalin {copied_count} file ke folder staging.")

    if zip_path.exists():
        zip_path.unlink()
    if checksum_path.exists():
        checksum_path.unlink()

    print(f"Membuat ZIP di {zip_path.as_posix()}...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, _, files in os.walk(staging_dir):
            root_path = Path(root)
            for file in files:
                file_path = root_path / file
                rel_path = file_path.relative_to(staging_dir)
                zipf.write(file_path, rel_path.as_posix())

    print("ZIP berhasil dibuat. Memulai verifikasi keamanan paket...")

    failed = False
    error_msg = ""
    with zipfile.ZipFile(zip_path, "r") as zipf:
        namelist = zipf.namelist()

        for name in namelist:
            path_parts = Path(name).parts

            for part in path_parts:
                if part in EXCLUDE_DIRS:
                    error_msg = f"Folder terlarang '{part}' ditemukan di dalam ZIP: {name}"
                    print(f"CRITICAL ERROR: {error_msg}", file=sys.stderr)
                    failed = True

            filename = Path(name).name
            if filename in EXCLUDE_FILES or filename == checksum_name:
                error_msg = f"File terlarang '{filename}' ditemukan di dalam ZIP: {name}"
                print(f"CRITICAL ERROR: {error_msg}", file=sys.stderr)
                failed = True

            suffix = Path(name).suffix.lower()
            if suffix in EXCLUDE_EXTENSIONS:
                error_msg = f"File dengan ekstensi terlarang '{suffix}' ditemukan di dalam ZIP: {name}"
                print(f"CRITICAL ERROR: {error_msg}", file=sys.stderr)
                failed = True
            if filename.startswith("test_") and filename.endswith(".db"):
                error_msg = f"Database pengujian ditemukan di dalam ZIP: {name}"
                print(f"CRITICAL ERROR: {error_msg}", file=sys.stderr)
                failed = True

        for req in REQUIRED_FILES:
            if req not in namelist:
                error_msg = f"File wajib '{req}' tidak ditemukan di dalam ZIP!"
                print(f"CRITICAL ERROR: {error_msg}", file=sys.stderr)
                failed = True

    clean_staging(staging_dir)

    if failed:
        print("=" * 60)
        print("VERIFIKASI GAGAL: Paket rilis mengandung file terlarang atau kekurangan file wajib!", file=sys.stderr)
        print("=" * 60)
        if zip_path.exists():
            zip_path.unlink()
        if checksum_path.exists():
            checksum_path.unlink()
        return ReleaseResult(success=False, error_message=error_msg)

    try:
        if checksum_path.exists():
            checksum_path.unlink()

        sha256_hash = calculate_sha256(zip_path)

        checksum_content = f"{sha256_hash}  {zip_name}\n"
        checksum_path.write_text(checksum_content, encoding="utf-8")

        written_content = checksum_path.read_text(encoding="utf-8").strip()
        written_parts = written_content.split()
        if not written_parts or written_parts[0] != sha256_hash:
            raise ValueError("Verifikasi checksum tertulis tidak cocok!")

        recalculated_hash = calculate_sha256(zip_path)
        if recalculated_hash != sha256_hash:
            raise ValueError("Hash ZIP berubah saat verifikasi!")

        print(f"SHA-256 Checksum: {sha256_hash}")
        print(f"Checksum file berhasil dibuat di: {checksum_path.as_posix()}")
    except Exception as e:
        err = f"Gagal membuat/memverifikasi checksum: {e}"
        print(f"CRITICAL ERROR: {err}", file=sys.stderr)
        if zip_path.exists():
            zip_path.unlink()
        if checksum_path.exists():
            checksum_path.unlink()
        return ReleaseResult(success=False, error_message=err)

    print("=" * 60)
    print("VERIFIKASI SUKSES: Paket bersih dan aman untuk produksi.")
    print(f"Lokasi paket: {zip_path.as_posix()}")
    print("=" * 60)
    return ReleaseResult(success=True, zip_path=zip_path, checksum_path=checksum_path)


if __name__ == "__main__":
    res = build_release()
    sys.exit(0 if res.success else 1)
