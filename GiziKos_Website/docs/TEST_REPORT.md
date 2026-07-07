# Laporan Pengujian GiziKos 3.2.3

Tanggal pengujian: 27 Juni 2026  
Lingkungan utama: Python 3.11+, FastAPI, SQLite

## Hasil otomatis

```text
52 passed
```

Skenario yang diuji:

1. **Halaman Publik & Health Check**: Memastikan endpoint `/api/health` dan halaman statis utama berfungsi.
2. **Autentikasi Akun**: Menguji pendaftaran, login, akses halaman `/akun`, dan proses logout.
3. **Validasi Login Rute Utama**: Menjamin rute `/cek-gizi` dan API rekomendasi memerlukan autentikasi login (guest ditolak dengan redirect atau status 401).
4. **Proteksi CSRF**: Memastikan permintaan POST tanpa token CSRF yang valid ditolak dengan status 403 Forbidden.
5. **Rate Limiting**: Memastikan ip/session limiter aktif memblokir request berturut-turut (>5 login/menit) dengan status 429.
6. **Otorisasi Admin Independen & Debug Payload Matrix (3.2.3)**: Memastikan otorisasi admin dan flag debug `GIZIKOS_ENABLE_DEBUG_PAYLOAD` dievaluasi tepat sesuai role (User, Admin, Guest, Production).
7. **Isolasi Database Pengujian & Build Release**: Menjamin seluruh unit test dan build release pengujian dijalankan di folder sementara (`tmp_path`) tanpa mengubah artifact rilis di root project.
8. **Denda Repetisi Algoritma & Keanekaragaman (3.2.3)**: Memverifikasi evaluasi kombinasi harian, denda repetisi, dan pembuktian integrasi keanekaragaman (Skenario A, B, C).
9. **Deteksi & Validasi Metadata Resep (3.2.3)**: Menguji deteksi 11 field resep & 8 field makanan, serta helper `validate_recipe_classification` untuk menangkap slug/kategori yang salah.
10. **Alergi & Preferensi Makanan**: Menjamin bahan berpenyakit (alergi, tidak disukai, pola vegetarian) disaring dari stok aktif.
11. **Pengecekan Alat**: Menjamin jika alat masak kosong, maka status diset `no_match` tanpa resep terkirim.
12. **Idempotensi & Koordinator Startup Migration (3.2.3)**: Memastikan koordinator `initialize_or_migrate_database` mem-backup database lama terlebih dahulu (`gizikos_before_3_2_3_*.db`), membatalkan migrasi jika backup gagal, dan menjaga data pengguna/konsultasi.
13. **Portion Scaling & Evaluasi Protein Netral (3.2.3)**: Menguji porsi makanan (`0.75x`, `1.0x`, `1.25x`, `1.5x`) serta evaluasi protein netral (`protein_evaluation_label`).
14. **Penyembunyian Skor Internal & Legacy Normalizer (3.2.3)**: Memastikan skor internal disembunyikan dari serializer pengguna biasa dan riwayat konsultasi lama dapat dinormalisasi dengan aman.
15. **Formulasi Skor Balanced vs Maintain (3.2.3)**: Memverifikasi pembedaan skor komponen tujuan `balanced` dan `maintain` pada output audit (`goal_component_scores`).
16. **Higiene Packaging & Checksum Eksternal**: Memastikan berkas ZIP rilis bersih dan berkas `.sha256` berada di luar ZIP serta cocok dengan hash ZIP aktual.

## Batas jaminan

Pengujian membuktikan aplikasi berjalan pada lingkungan terisolasi di atas. Gunakan virtual environment dengan dependency yang tertera pada `requirements.txt` dan jalankan `.venv\Scripts\python -m pytest -v` setelah melakukan perubahan kode.

