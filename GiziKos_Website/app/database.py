from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from typing import Any


from sqlalchemy import create_engine, inspect, text, select
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import DATABASE_URL


class Base(DeclarativeBase):
    pass


engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


@dataclass
class RecipeMetadataValidation:
    is_valid: bool
    invalid_fields: list[str]
    expected_main_protein: str | None
    expected_main_carbohydrate: str | None
    expected_vegetable_group: str


def validate_recipe_classification(
    recipe: Any,
    ingredients: list[Any],
) -> RecipeMetadataValidation:
    """Helper terpusat untuk memverifikasi konsistensi main_protein, main_carbohydrate, dan vegetable_group."""
    invalid_fields: list[str] = []
    main_prot_slug: str | None = None
    main_prot_grams = 0.0
    main_carb_slug: str | None = None
    main_carb_grams = 0.0
    veg_list: list[str] = []

    recipe_food_slugs = set()
    for ing in ingredients:
        food = getattr(ing, "food", None)
        if not food:
            continue
        recipe_food_slugs.add(food.slug)
        grams = float(getattr(ing, "grams", 0.0))
        if food.category == "protein" and grams > main_prot_grams:
            main_prot_slug = food.slug
            main_prot_grams = grams
        elif food.category == "pokok" and grams > main_carb_grams:
            main_carb_slug = food.slug
            main_carb_grams = grams
        elif food.category == "sayur":
            veg_list.append(food.slug)

    expected_veg = ";".join(sorted(veg_list)) if veg_list else "none"

    curr_prot = (getattr(recipe, "main_protein", None) or "").strip()
    if curr_prot != (main_prot_slug or ""):
        invalid_fields.append("main_protein")

    curr_carb = (getattr(recipe, "main_carbohydrate", None) or "").strip()
    if curr_carb != (main_carb_slug or ""):
        invalid_fields.append("main_carbohydrate")

    curr_veg = (getattr(recipe, "vegetable_group", None) or "").strip()
    if curr_veg != expected_veg:
        invalid_fields.append("vegetable_group")

    return RecipeMetadataValidation(
        is_valid=len(invalid_fields) == 0,
        invalid_fields=invalid_fields,
        expected_main_protein=main_prot_slug,
        expected_main_carbohydrate=main_carb_slug,
        expected_vegetable_group=expected_veg,
    )


@dataclass
class MetadataSyncPlan:
    requires_recipe_sync: bool
    requires_food_sync: bool
    requires_schema_upgrade: bool
    requires_backup: bool
    reasons: list[str]

    @property
    def needs_migration(self) -> bool:
        return self.requires_recipe_sync or self.requires_food_sync or self.requires_schema_upgrade


def database_has_existing_application_data(session: Session) -> bool:
    """Memeriksa apakah database sudah memiliki data aplikasi nyata (user, consultation, resep, dll)."""
    try:
        inspector = inspect(session.bind)
        tables = inspector.get_table_names()

        for table in ["users", "consultations", "recipes", "foods"]:
            if table in tables:
                res = session.execute(text(f"SELECT COUNT(*) FROM {table}")).fetchone()
                if res and res[0] > 0:
                    return True
        if "system_settings" in tables:
            res = session.execute(text("SELECT COUNT(*) FROM system_settings WHERE key != 'dataset_version'")).fetchone()
            if res and res[0] > 0:
                return True
    except Exception:
        pass
    return False


def detect_metadata_sync_requirements(session: Session) -> MetadataSyncPlan:
    """Helper terpusat untuk mendeteksi kebutuhan migrasi skema dan sinkronisasi metadata."""
    reasons: list[str] = []
    requires_recipe_sync = False
    requires_food_sync = False
    requires_schema_upgrade = False

    bind = session.bind if session else engine
    inspector = inspect(bind)
    tables = inspector.get_table_names()

    if "recipes" not in tables or "foods" not in tables or "consultations" not in tables:
        requires_schema_upgrade = True
        reasons.append("Tabel inti (recipes/foods/consultations) belum lengkap.")
    else:
        consultation_cols = {c["name"] for c in inspector.get_columns("consultations")}
        if "user_id" not in consultation_cols:
            requires_schema_upgrade = True
            reasons.append("Kolom consultations.user_id belum ada.")

        recipe_cols = {c["name"] for c in inspector.get_columns("recipes")}
        required_recipe_cols = {
            "source_name", "source_reference", "verification_status", "data_year",
            "price_location", "price_date", "food_state", "portion_reference",
            "main_protein", "main_carbohydrate", "vegetable_group"
        }
        missing_recipe_cols = required_recipe_cols - recipe_cols
        if missing_recipe_cols:
            requires_schema_upgrade = True
            reasons.append(f"Kolom resep belum lengkap: {missing_recipe_cols}")

        food_cols = {c["name"] for c in inspector.get_columns("foods")}
        required_food_cols = {
            "source_name", "source_reference", "verification_status", "data_year",
            "price_location", "price_date", "food_state", "portion_reference"
        }
        missing_food_cols = required_food_cols - food_cols
        if missing_food_cols:
            requires_schema_upgrade = True
            reasons.append(f"Kolom makanan belum lengkap: {missing_food_cols}")

    if not requires_schema_upgrade:
        try:
            res = session.execute(text("SELECT value FROM system_settings WHERE key = 'dataset_version'")).fetchone()
            if res is None or res[0] != "2.2":
                requires_recipe_sync = True
                requires_food_sync = True
                reasons.append("Dataset version belum 2.2.")
        except Exception:
            requires_schema_upgrade = True
            reasons.append("Tabel system_settings belum tersedia.")

    if not requires_schema_upgrade:
        try:
            recipe_check = session.execute(text("""
                SELECT COUNT(*) FROM recipes 
                WHERE main_protein IS NULL OR TRIM(main_protein) = ''
                   OR main_carbohydrate IS NULL OR TRIM(main_carbohydrate) = ''
                   OR vegetable_group IS NULL OR TRIM(vegetable_group) = ''
                   OR source_name IS NULL OR TRIM(source_name) = ''
                   OR source_reference IS NULL OR TRIM(source_reference) = ''
                   OR verification_status IS NULL OR TRIM(verification_status) = ''
                   OR data_year IS NULL
                   OR price_location IS NULL OR TRIM(price_location) = ''
                   OR price_date IS NULL OR TRIM(price_date) = ''
                   OR food_state IS NULL OR TRIM(food_state) = ''
                   OR portion_reference IS NULL OR TRIM(portion_reference) = ''
            """)).fetchone()
            if recipe_check and recipe_check[0] > 0:
                requires_recipe_sync = True
                reasons.append(f"Terdapat {recipe_check[0]} resep dengan metadata kosong/NULL/spasi.")
        except Exception:
            requires_recipe_sync = True

        try:
            food_check = session.execute(text("""
                SELECT COUNT(*) FROM foods
                WHERE source_name IS NULL OR TRIM(source_name) = ''
                   OR source_reference IS NULL OR TRIM(source_reference) = ''
                   OR verification_status IS NULL OR TRIM(verification_status) = ''
                   OR data_year IS NULL
                   OR price_location IS NULL OR TRIM(price_location) = ''
                   OR price_date IS NULL OR TRIM(price_date) = ''
                   OR food_state IS NULL OR TRIM(food_state) = ''
                   OR portion_reference IS NULL OR TRIM(portion_reference) = ''
            """)).fetchone()
            if food_check and food_check[0] > 0:
                requires_food_sync = True
                reasons.append(f"Terdapat {food_check[0]} makanan dengan metadata kosong/NULL/spasi.")
        except Exception:
            requires_food_sync = True

        # Validasi klasifikasi resep (Masalah 2)
        if not requires_recipe_sync:
            from sqlalchemy.orm import selectinload
            from .models import Recipe, RecipeIngredient
            recipes = session.scalars(select(Recipe).options(selectinload(Recipe.ingredients).joinedload(RecipeIngredient.food))).all()
            invalid_count = 0
            for r in recipes:
                val = validate_recipe_classification(r, r.ingredients)
                if not val.is_valid:
                    invalid_count += 1
            if invalid_count > 0:
                requires_recipe_sync = True
                reasons.append(f"Terdapat {invalid_count} resep dengan klasifikasi metadata yang tidak konsisten/salah.")


    has_data = database_has_existing_application_data(session)
    requires_backup = (requires_recipe_sync or requires_food_sync or requires_schema_upgrade) and has_data

    return MetadataSyncPlan(
        requires_recipe_sync=requires_recipe_sync,
        requires_food_sync=requires_food_sync,
        requires_schema_upgrade=requires_schema_upgrade,
        requires_backup=requires_backup,
        reasons=reasons,
    )


@dataclass
class MigrationResult:
    status: str
    backup_created: bool
    backup_path: str | None
    reasons: list[str]


def initialize_or_migrate_database() -> MigrationResult:
    """Koordinator tunggal urutan startup & migrasi database."""
    import shutil
    import sqlite3
    from datetime import datetime
    from pathlib import Path
    from .config import DB_PATH

    db_existed = DB_PATH.exists()
    backup_created = False
    backup_path_str: str | None = None

    with Session(engine) as session:
        plan = detect_metadata_sync_requirements(session)

    if plan.requires_backup and db_existed:
        backup_dir = DB_PATH.parent / "backups"
        backup_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = backup_dir / f"gizikos_before_3_2_3_{timestamp}.db"
        try:
            shutil.copy2(DB_PATH, backup_file)
            conn = sqlite3.connect(backup_file)
            conn.execute("SELECT name FROM sqlite_master LIMIT 1")
            conn.close()
            backup_created = True
            backup_path_str = backup_file.as_posix()
            print(f"Informasi: Backup migrasi berhasil dibuat di {backup_path_str}")
        except Exception as e:
            print(f"Peringatan: Gagal mencadangkan database sebelum migrasi: {e}")
            raise RuntimeError(f"Database migration aborted: Gagal membuat backup database sebelum migrasi: {e}")

    # 5. Buat tabel yang belum ada
    Base.metadata.create_all(bind=engine)

    # 6. Jalankan perubahan schema
    upgrade_schema(skip_backup=True)

    # 7 & 9 & 10. Jalankan sinkronisasi metadata & seed
    from .seed import seed_database
    with Session(engine) as session:
        seed_database(session)

    return MigrationResult(
        status="success",
        backup_created=backup_created,
        backup_path=backup_path_str,
        reasons=plan.reasons,
    )


def upgrade_schema(skip_backup: bool = False) -> None:
    """Upgrade ringan untuk pengguna yang membawa database versi 1.x / 2.x."""
    import os
    import shutil
    from datetime import datetime
    from pathlib import Path
    from .config import DB_PATH, BASE_DIR

    if DB_PATH.exists() and not skip_backup:
        with Session(engine) as session:
            plan = detect_metadata_sync_requirements(session)

        if plan.requires_backup:
            backup_dir = DB_PATH.parent / "backups"
            try:
                backup_dir.mkdir(exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_file = backup_dir / f"gizikos_before_3_2_3_{timestamp}.db"
                shutil.copy2(DB_PATH, backup_file)

                import sqlite3
                conn = sqlite3.connect(backup_file)
                conn.execute("SELECT name FROM sqlite_master LIMIT 1")
                conn.close()

                print(f"Informasi: Backup migrasi berhasil dibuat di {backup_file.as_posix()}")
            except Exception as e:
                print(f"Peringatan: Gagal mencadangkan database sebelum migrasi: {e}")
                raise RuntimeError(f"Database migration aborted: Gagal membuat backup database sebelum migrasi: {e}")

    inspector = inspect(engine)
    tables = inspector.get_table_names()

    if "consultations" in tables:
        columns = {column["name"] for column in inspector.get_columns("consultations")}
        if "user_id" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE consultations ADD COLUMN user_id VARCHAR(36)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS ix_consultations_user_id ON consultations (user_id)"))

    if "foods" in tables:
        columns = {column["name"] for column in inspector.get_columns("foods")}
        new_cols = [
            ("source_name", "VARCHAR(100)"),
            ("source_reference", "VARCHAR(200)"),
            ("verification_status", "VARCHAR(50) DEFAULT 'demo'"),
            ("data_year", "INTEGER"),
            ("price_location", "VARCHAR(100)"),
            ("price_date", "VARCHAR(50)"),
            ("food_state", "VARCHAR(50)"),
            ("portion_reference", "VARCHAR(100)"),
        ]
        with engine.begin() as connection:
            for col_name, col_type in new_cols:
                if col_name not in columns:
                    connection.execute(text(f"ALTER TABLE foods ADD COLUMN {col_name} {col_type}"))

    if "recipes" in tables:
        columns = {column["name"] for column in inspector.get_columns("recipes")}
        new_cols = [
            ("source_name", "VARCHAR(100)"),
            ("source_reference", "VARCHAR(200)"),
            ("verification_status", "VARCHAR(50) DEFAULT 'demo'"),
            ("data_year", "INTEGER"),
            ("price_location", "VARCHAR(100)"),
            ("price_date", "VARCHAR(50)"),
            ("food_state", "VARCHAR(50)"),
            ("portion_reference", "VARCHAR(100)"),
            ("main_protein", "VARCHAR(100)"),
            ("main_carbohydrate", "VARCHAR(100)"),
            ("vegetable_group", "VARCHAR(200)"),
        ]
        with engine.begin() as connection:
            for col_name, col_type in new_cols:
                if col_name not in columns:
                    connection.execute(text(f"ALTER TABLE recipes ADD COLUMN {col_name} {col_type}"))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

