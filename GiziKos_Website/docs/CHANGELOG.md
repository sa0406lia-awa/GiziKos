# Changelog

## 3.2.3

- Deteksi terpusat metadata resep (11 field) dan makanan (8 field) lengkap pada `detect_metadata_sync_requirements`.
- Implementasi `validate_recipe_classification` untuk memeriksa konsistensi `main_protein`, `main_carbohydrate`, dan `vegetable_group` terhadap bahan resep aktual.
- Restrukturisasi startup database dengan satu koordinator `initialize_or_migrate_database` yang menjamin pembuatan dan verifikasi backup SQLite sebelum `create_all` atau `ALTER TABLE`.
- Penguatan pengujian integrasi portion scaling dan keanekaragaman (diversity) dengan dataset terkontrol.
- Matriks keamanan debug payload audit (`GIZIKOS_ENABLE_DEBUG_PAYLOAD`) berbasis role (User, Admin, Guest, Production).
- Penghapusan skor internal dari serializer pengguna biasa (`serialize_consultation_for_user`) dan penambahan normalizer riwayat konsultasi lama (`normalize_legacy_consultation_result`).
- Evaluasi kecukupan protein netral (`protein_evaluation_label`) tanpa klaim medis/klinis.
- Formulasi skor komponen terpisah untuk tujuan `balanced` vs `maintain` serta menyertakannya pada output audit (`goal_component_scores`).
- Total 52 automated tests lulus secara komprehensif.

## 3.2.2


- Perbaikan script build release (`build_release.py`) dengan mengonfirmasi exclusion berkas checksum eksternal (`*.sha256`) agar tidak masuk ke dalam ZIP produksi.
- Parameterisasi `build_release(source_dir, output_dir)` dan penggunaan `ReleaseResult` untuk mendukung pengujian terisolasi di `tmp_path` tanpa mengubah artifact release asli di root project.
- Implementasi helper terpusat `detect_metadata_sync_requirements` untuk mengaudit seluruh field metadata `Recipe` dan `Food`, serta menjamin pembuatan backup otomatis `gizikos_before_3_2_2_*.db` jika terdapat data aplikasi lama sebelum migrasi dilakukan.
- Standarisasi resep tanpa bahan sayuran menggunakan nilai sentinel eksplisit `vegetable_group = "none"` yang diabaikan dari perhitungan keanekaragaman dan tidak ditampilkan ke pengguna.
- Pembenahan otorisasi admin pada route `/hasil/{id}` dan `/api/consultations/{id}` agar sesi admin aktif dapat melihat konsultasi untuk audit tanpa memerlukan sesi user biasa.
- Pembuktian portion scaling harian dan keanekaragaman melalui integration test komprehensif (Scenario A, B, C).
- Pemisahan eksplisit `energy_ratio_percent` / `protein_ratio_percent` dan adequacy score pada payload API dan tampilan UI.
- Pengontrolan debug payload audit dengan flag `GIZIKOS_ENABLE_DEBUG_PAYLOAD=false`.
- Penyelarasan pesan status rekomendasi (`complete`, `partial`, `no_match`) dan penambahan disclaimer rujukan prototipe konsisten.
- Penambahan test suite `test_audit_3_2_2.py` (total 41 automated tests lulus).

## 3.2.1

- Pembenahan mekanisme migrasi versi 2.2 agar mendeteksi integritas data (kolom dengan nilai NULL/kosong) secara presisi, bukan hanya versi dataset.
- Implementasi fungsi sinkronisasi metadata parsial yang idempotent dan aman tanpa menghapus akun/konsultasi pengguna.
- Perbaikan logic backup migrasi database agar tidak membuat backup kosong saat fresh install menggunakan helper `database_has_existing_application_data`.
- Relokasi folder backup migrasi agar selalu seinduk dengan lokasi database (`DB_PATH.parent / "backups"`), disertai dengan verifikasi integritas SQLite.
- Peningkatan portion scaling agar mengevaluasi seluruh varian porsi (recipe + multiplier) yang valid di tahap kombinasi harian, membatasi kandidat maksimal 16 per waktu makan secara deterministik.
- Penguatan variasi menu harian dengan menaikkan bobot diversity ke 0.12 dan penambahan tie-breaker kombinasi untuk skor dekat (<= 2 poin).
- Penyajian detail analisis kombinasi lengkap (`combination_analysis`) di payload hasil rekomendasi.
- Pengamanan payload debug candidates ditolak (`rejected_candidates_debug`) dan internal tracing agar tidak bocor ke pengguna non-admin di lingkungan produksi.
- Pembuatan checksum SHA-256 otomatis dan validasinya pada script build rilis `scripts/build_release.py`.
- Penambahan dokumentasi rilis docs/RELEASE_GUIDE.md dan sinkronisasi versi 3.2.1 ke seluruh file project.

## 3.2.0

- Perbaikan logika repitition penalty (denda repetisi) agar hanya dihitung sekali pada diversity score, mencegah pengurangan ganda.
- Penyesuaian skor tujuan "lose" (menurunkan berat badan) berbasis kedekatan deficit energi untuk mencegah promosi defisit berlebih.
- Implementasi Portion Scaling otomatis (0.75x, 1.0x, 1.25x, 1.5x) untuk meningkatkan kecukupan target energi menu harian.
- Pemisahan data inferensi menjadi selected_rules, rejection_summary, dan debug rejected candidates.
- Keamanan admin ditingkatkan dengan rate limiter login admin dan pemeriksaan ketat pada environment produksi.
- Pembaruan mekanisme migrasi database idempotent, otomatis backup database lama sebelum peningkatan, dan pengamanan harga kustom admin.
- Pembaruan metadata verifikasi dataset ("demo") dan sinkronisasi statis rilis (59 bahan, 52 resep).
- Penambahan 9 test case baru (total 27 tests passed).
- Pengemasan rilis bersih (higiene ZIP) bebas dari file .env, cache, database, dan virtual environment.

## 3.1.0

- Penambahan proteksi CSRF (`verify_csrf`) pada seluruh form POST.
- Penambahan in-memory rate limiting pada route POST /daftar dan /login.
- Implementasi evaluasi kombinasi dua tahap dengan evaluasi gizi, alokasi sisa anggaran, dan denda repetisi menu (protein, karbohidrat, sayur, metode masak).
- Penambahan metadata dataset dan klasifikasi bahan makanan pada tabel `foods` dan `recipes`.
- Pemisahan context aturan aktif pada halaman hasil (aturan pendukung, aturan penolak, dan ringkasan penolakan).
- Isolasi database pengujian (`tests/conftest.py`) sehingga database utama aman saat pengujian dijalankan.
- Penambahan 7 test case baru (total 18 tests passed).
- Penyempurnaan dokumentasi disclaimer dataset akademis/prototipe dan asumsi stok porsi makan.

## 3.0.0

- Design system desktop dan mobile ditulis ulang.
- Header, footer, landing page, form, hasil, autentikasi, akun, dan admin dibuat lebih konsisten.
- Tipografi, grid, spasi, kartu, tombol, dan warna dirapikan.
- Cache-busting CSS dan JavaScript ditambahkan agar browser tidak memakai aset versi lama.
- Launcher otomatis mencari port kosong ketika port 8000 sedang dipakai.
- Browser dibuka otomatis saat server siap.
- Instalasi dependency Windows tidak diulang setiap aplikasi dijalankan.
- Seluruh pengujian diperbarui untuk versi 3.0.0.

## 2.0.0

- Login, daftar, akun, dan riwayat.
- Enam tujuan pengguna.
- Slider anggaran dan preset.
- Alat masak diperluas.
- Sinkronisasi alergi, bahan tidak disukai, vegetarian, dan stok.
- 59 bahan dan 45 resep.
