# Input dan Output GiziKos 3.2.3

## Input konsultasi

| Field | Tipe | Batas/opsi |
|---|---|---|
| name | teks | 1–80 karakter |
| age | integer | 18–60; target utama 18–25 |
| gender | enum | male/female |
| weight | angka | 31–199 kg |
| height | angka | 131–219 cm |
| activity_level | enum | low/medium/high |
| goal | enum | balanced/maintain/lose/gain/muscle/hemat |
| daily_budget | integer | Rp15.000–Rp150.000; UI memakai slider/preset |
| meal_count | enum | 2 atau 3 |
| max_cooking_time | integer | 5–120 menit |
| tools | daftar | kompor, panci, wajan, rice-cooker, ketel-listrik, microwave, air-fryer, sandwich-maker, blender, pisau-talenan |
| allergies | daftar | peanut, egg, milk, seafood, gluten, soy |
| vegetarian | boolean | ovo-lakto: telur dan susu diperbolehkan |
| disliked_ingredients | daftar | slug bahan dari dataset |
| available_ingredients | daftar | slug bahan aman dari API `/api/foods` |
| has_medical_condition | boolean | memicu blok ruang lingkup |
| consent | boolean | wajib true |

## Sinkronisasi alergi dan stok

Frontend menyembunyikan bahan yang:

- memiliki alergen terpilih;
- masuk daftar bahan tidak disukai;
- nonvegetarian ketika pengguna memilih vegetarian.

Backend mengulang validasi tersebut dan menghapus stok terlarang dari payload sebelum inferensi.

## Output utama pengguna (Safe Payload)

- status dan pesan rujukan prototipe;
- profil, BMI informatif, dan estimasi kebutuhan energi;
- menu per waktu makan beserta portion scaling multiplier;
- bahan, gram, alat, waktu, dan langkah;
- estimasi energi, protein, lemak, karbohidrat, serat, serta biaya;
- `energy_ratio_percent` (pemenuhan energi %) dan `protein_ratio_percent` (pemenuhan protein %);
- label evaluasi protein netral (`di bawah target model`, `sesuai rentang model`, `di atas target model`);
- `diversity_label` (Sangat Beragam / Cukup Beragam / Kurang Beragam);
- alasan menu dipilih;
- alternatif menu sejenis;
- daftar belanja ringkas;
- aturan Forward Chaining yang aktif pendukung menu;
- ringkasan penolakan;
- peringatan variasi dan disclaimer prototipe.
*(Catatan: Skor internal seperti adequacy scores, raw fuzzy memberships, dan debug candidates ditolak disembunyikan dari pengguna biasa dan hanya tersedia untuk sesi Admin Audit).*

