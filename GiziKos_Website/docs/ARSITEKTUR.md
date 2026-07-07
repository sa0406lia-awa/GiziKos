# Arsitektur GiziKos 3.2.3

```text
Browser
  ├─ halaman publik
  ├─ daftar/login/akun
  └─ form konsultasi empat langkah
          ↓
FastAPI + Jinja2 + CSS + JavaScript
  ├─ Otorisasi Admin Audit (independen)
  └─ Security Debug Flag (GIZIKOS_ENABLE_DEBUG_PAYLOAD)
          ↓
Session authentication
  ├─ akun pengguna
  └─ admin knowledge base
          ↓
Validasi Pydantic + sinkronisasi pembatasan
  ├─ alergi
  ├─ bahan tidak disukai
  └─ vegetarian vs stok
          ↓
Forward Chaining Engine
  ├─ ruang lingkup
  ├─ kondisi medis
  ├─ tidak ada alat
  ├─ alergi/preferensi
  ├─ alat
  ├─ waktu
  └─ anggaran
          ↓
Fuzzy Scoring & Combination Evaluator
  ├─ budget fit
  ├─ time fit
  ├─ ingredient coverage
  ├─ nutrition balance
  ├─ goal compatibility (balanced vs maintain explicit)
  └─ daily diversity tie-breaker & sentinel ("none")
          ↓
Recommendation Service
  ├─ menu harian & portion scaling
  ├─ energy_ratio_percent & protein_ratio_percent (netral)
  ├─ total gizi/biaya
  ├─ shopping list
  ├─ explanation facility
  └─ safe user serialization & legacy normalizer
          ↓
SQLite + Startup Migration Coordinator (initialize_or_migrate_database)
  ├─ automatic backup before schema/table changes (gizikos_before_3_2_3_*.db)
  ├─ metadata classification validation (validate_recipe_classification)
  ├─ users
  ├─ consultations
  ├─ foods (8 metadata fields)
  ├─ recipes (11 metadata fields)
  └─ settings
```


## Hard constraint

- usia di luar ruang lingkup;
- kondisi medis khusus;
- tidak ada alat masak;
- alergen;
- vegetarian;
- bahan tidak disukai;
- alat resep tidak tersedia;
- waktu memasak berlebih;
- biaya melebihi alokasi.

## Soft constraint

Menu yang lolos diberi skor berdasarkan kesesuaian anggaran, waktu, cakupan bahan, keseimbangan nutrisi, dan tujuan utama pengguna. Evaluasi harian mempertimbangkan denda repetisi danSentinel `vegetable_group = "none"`.
