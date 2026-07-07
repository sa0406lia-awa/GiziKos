# Panduan Rilis Produksi GiziKos 3.2.3


Dokumen ini menjelaskan tata cara pembuatan rilis paket produksi GiziKos secara aman dan bersih, serta proses verifikasi integritas paket.

## File yang Boleh Dibagikan

Saat merilis versi baru ke pengguna atau server produksi, **HANYA** dua file berikut yang boleh didistribusikan:
1. `GiziKos_Website_Final_Production.zip` (Paket web aplikasi bersih)
2. `GiziKos_Website_Final_Production.zip.sha256` (Checksum hash SHA-256)

> [!CAUTION]
> **DILARANG KERAS** membagikan ZIP dari seluruh workspace pengembangan. ZIP workspace mengandung file sensitif seperti database aktif pengguna, file cadangan lama, kredensial `.env`, cache pengujian, dan virtual environment `.venv`.

---

## Daftar File Terlarang (Tidak Boleh Masuk ZIP)

Berikut adalah komponen yang otomatis disaring dan dilarang masuk ke dalam paket produksi final:
- Database aktif (`gizikos.db`, `gizikos_backup.db`, `test_*.db`)
- Folder backup (`backups/`)
- Virtual environment (`.venv/`, `venv/`)
- Git repository (`.git/`)
- File konfigurasi environment sensitif (`.env`)
- Cache python (`__pycache__/`, `.pytest_cache/`, `.coverage`, `htmlcov/`)
- Aset editor (`.idea/`, `.vscode/`)

---

## Cara Menjalankan Build Release

Untuk membuat paket rilis bersih, ikuti langkah berikut:

1. Buka PowerShell atau Command Prompt pada folder `GiziKos_Website`.
2. Pastikan virtual environment aktif.
3. Jalankan script build release:
   ```bash
   python scripts/build_release.py
   ```
4. Script akan melakukan tahapan:
   - Membuat folder staging sementara.
   - Menyaring dan menyalin file proyek yang aman.
   - Membuat file ZIP `GiziKos_Website_Final_Production.zip`.
   - Memverifikasi isi ZIP terhadap file wajib dan file terlarang.
   - Menghasilkan checksum SHA-256 otomatis ke `GiziKos_Website_Final_Production.zip.sha256`.

---

## Verifikasi Checksum SHA-256

Setelah proses selesai, hash SHA-256 akan tercetak pada terminal. Untuk memverifikasi keabsahan berkas ZIP secara manual:

### Di Windows (PowerShell)
```powershell
Get-FileHash .\GiziKos_Website_Final_Production.zip -Algorithm SHA256
```
Bandingkan hash yang muncul dengan isi dari file `GiziKos_Website_Final_Production.zip.sha256`.

### Di Linux / macOS
```bash
sha256sum -c GiziKos_Website_Final_Production.zip.sha256
```
Output harus bertuliskan `GiziKos_Website_Final_Production.zip: OK`.

---

## Cara Memeriksa Isi ZIP Secara Manual

Sebelum mengunggah berkas, Anda dapat membuka file ZIP untuk memastikan struktur berikut terbentuk di root ZIP:
- `app/` (Berisi source code FastAPI)
- `data/` (Berisi `foods.csv` dan `recipes.json`)
- `docs/` (Berisi panduan arsitektur dan rilis)
- `pyproject.toml`
- `requirements.txt`
- `run.py`
- `start.bat` (Launcher untuk pengguna Windows)

Pastikan tidak ada folder `.venv`, file `gizikos.db`, atau folder `backups/` di dalam ZIP tersebut.
