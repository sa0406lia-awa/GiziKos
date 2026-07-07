# GiziKos 3.2.3 — Website Sistem Pakar Menu Anak Kos

GiziKos adalah website sistem pakar berbasis FastAPI untuk merekomendasikan menu anak kos berdasarkan profil, tujuan, anggaran, aktivitas, alergi, preferensi, alat memasak, waktu, dan bahan tersedia.

## Menjalankan di Windows

1. Ekstrak ZIP ke folder baru. Jangan menimpa folder versi lama.
2. Pastikan Python 3.11 atau lebih baru tersedia.
3. Klik dua kali `start.bat`.
4. Pada penggunaan pertama, dependency akan dipasang otomatis.
5. Browser akan terbuka otomatis. Bila tidak, gunakan alamat yang ditampilkan pada jendela CMD.

Jika port 8000 sedang dipakai, aplikasi otomatis memilih 8001, 8002, dan seterusnya.

## Menjalankan manual

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python run.py
```

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python run.py
```

## Halaman utama

- `/` — beranda
- `/cek-gizi` — form konsultasi
- `/login` — login
- `/daftar` — pendaftaran
- `/akun` — akun pengguna
- `/riwayat` — riwayat rekomendasi
- `/metode` — penjelasan sistem pakar
- `/referensi` — dataset dan referensi
- `/admin` — dashboard admin
- `/docs` — dokumentasi API

## Admin demo

Password bawaan: `gizikos-admin`

Ubah sebelum deployment melalui environment variable `GIZIKOS_ADMIN_PASSWORD`.

## Algoritma & Sistem Pakar

GiziKos menggunakan metode **Fuzzy Sugeno orde nol (weighted average berbasis singleton output)** untuk defuzzifikasi kecocokan relatif menu. Rencana harian dievaluasi dengan evaluasi kombinasi dua tahap:
1. Menyaring kandidat menu per waktu makan menggunakan aturan wajib (Forward Chaining).
2. Mengevaluasi seluruh kombinasi harian dengan pembobotan gizi, sisa anggaran, dan denda repetisi (protein, karbohidrat, sayur, metode masak) demi menjamin variasi menu harian yang seimbang.

## Dataset & Asumsi Bahan Tersedia

- **Dataset Prototipe**: Dataset bahan (`foods.csv`) dan menu (`recipes.json`) disusun sebagai prototipe demonstrasi fungsional sistem pakar, bukan rujukan klinis atau program atlet profesional. Dataset dilengkapi metadata sumber, verifikasi, lokasi, dan tahun data.
- **Asumsi Stok Bahan**: Pilihan "bahan tersedia" berasumsi bahwa pengguna memiliki bahan tersebut dalam jumlah yang cukup untuk satu porsi makan. Asumsi ini digunakan untuk perhitungan biaya belanja dan sisa anggaran.

## Keamanan & Keandalan Hardening 3.2.3

- Proteksi CSRF (`verify_csrf`) dan in-memory Rate Limiting (`rate_limiter`) diaktifkan pada semua form POST.
- Pemeriksaan kekuatan kredensial di mode produksi.
- Koordinator migrasi startup dan pembuatan backup otomatis sebelum perubahan struktur/tabel database.
- Otorisasi admin independen untuk audit konsultasi tanpa memerlukan sesi pengguna biasa.
- Pembatasan payload debug audit dengan flag eksplisit `GIZIKOS_ENABLE_DEBUG_PAYLOAD`.
- Penyembunyian skor internal dari serializer pengguna biasa serta dukungan normalizer riwayat lama.

## Pengujian

Untuk menjalankan pengujian otomatis:
```bash
.venv\Scripts\python -m pytest -v
```

Hasil rilis: **52 pengujian lulus** (meliputi migrasi metadata 2.2 lengkap, validasi klasifikasi resep, portion scaling harian, denda repetisi diversity, CSRF, rate-limit, validasi otorisasi admin, payload security matrix, dan evaluasi protein netral).

## Spesifikasi Rilis 3.2.3

- **Nama Aplikasi**: GiziKos 3.2.3
- **Pengujian**: 52 automated tests (seluruhnya lulus)
- **Dataset**: 59 bahan makanan dan 52 resep menu (dataset prototipe/demo)
- **Metode**: Fuzzy Sugeno orde nol (weighted average singleton) & Forward Chaining

## Ruang Lingkup & Disclaimer

GiziKos merupakan sistem rekomendasi prototipe dan bukan pengganti konsultasi dokter atau ahli gizi.

