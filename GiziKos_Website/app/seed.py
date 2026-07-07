from __future__ import annotations

import csv
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import DATA_DIR
from .database import Base, engine, detect_metadata_sync_requirements
from .models import Food, Recipe, RecipeIngredient, SystemSetting

DATASET_VERSION = "2.2"

DEFAULT_SETTINGS = {
    "age_min": ("18", "Usia minimum pengguna umum GiziKos."),
    "age_max": ("25", "Usia maksimum target utama mahasiswa kos."),
    "min_ingredient_coverage": ("0.20", "Cakupan minimal bahan yang tersedia agar resep tetap dipertimbangkan."),
    "budget_tolerance": ("1.00", "Toleransi biaya terhadap anggaran per waktu makan."),
    "medical_scope_block": ("true", "Blokir rekomendasi untuk kondisi medis khusus."),
}


def sync_recipe_classification_metadata(db: Session) -> None:
    """Mengisi & memvalidasi klasifikasi resep (main_protein, main_carbohydrate, vegetable_group) serta metadata pendukung."""
    from .database import validate_recipe_classification
    recipes = db.scalars(select(Recipe)).all()

    for recipe in recipes:
        val = validate_recipe_classification(recipe, recipe.ingredients)
        if not val.is_valid:
            recipe.main_protein = val.expected_main_protein
            recipe.main_carbohydrate = val.expected_main_carbohydrate
            recipe.vegetable_group = val.expected_vegetable_group

        # Isi metadata wajib jika kosong/NULL/spasi
        if not recipe.source_name or not recipe.source_name.strip():
            recipe.source_name = "Rancangan Menu GiziKos"
        if not recipe.source_reference or not recipe.source_reference.strip():
            recipe.source_reference = "demo"
        if not recipe.verification_status or not recipe.verification_status.strip():
            recipe.verification_status = "demo"
        if not recipe.data_year:
            recipe.data_year = 2026
        if not recipe.price_location or not recipe.price_location.strip():
            recipe.price_location = "Estimasi"
        if not recipe.price_date or not recipe.price_date.strip():
            recipe.price_date = "Estimasi"
        if not recipe.food_state or not recipe.food_state.strip():
            recipe.food_state = "cooked"
        if not recipe.portion_reference or not recipe.portion_reference.strip():
            recipe.portion_reference = "1 porsi"

    db.commit()


def sync_food_source_metadata(db: Session) -> None:
    """Mengisi metadata makanan yang kosong, NULL, atau spasi."""
    foods = db.scalars(select(Food)).all()

    for food in foods:
        if not food.source_name or not food.source_name.strip():
            food.source_name = "Estimasi GiziKos"
        if not food.source_reference or not food.source_reference.strip():
            food.source_reference = "demo"
        if not food.verification_status or not food.verification_status.strip():
            food.verification_status = "demo"
        if not food.food_state or not food.food_state.strip():
            food.food_state = "processed" if food.category == "bumbu" else "raw"
        if not food.data_year:
            food.data_year = 2026
        if not food.price_location or not food.price_location.strip():
            food.price_location = "Estimasi"
        if not food.price_date or not food.price_date.strip():
            food.price_date = "Estimasi"
        if not food.portion_reference or not food.portion_reference.strip():
            food.portion_reference = "100g"

    db.commit()



def seed_database(db: Session, force: bool = False, force_metadata_sync: bool = False) -> None:
    Base.metadata.create_all(bind=engine)

    if force:
        for model in (RecipeIngredient, Recipe, Food, SystemSetting):
            db.query(model).delete()
        db.commit()

    dataset_setting = db.get(SystemSetting, "dataset_version")
    plan = detect_metadata_sync_requirements(db)

    should_sync = (
        force 
        or force_metadata_sync 
        or plan.needs_migration
    )
    
    if not should_sync:
        should_sync = db.scalar(select(Food.id).limit(1)) is None or db.scalar(select(Recipe.id).limit(1)) is None

    if should_sync:
        is_empty = db.scalar(select(Food.id).limit(1)) is None or db.scalar(select(Recipe.id).limit(1)) is None
        if is_empty or force or dataset_setting is None or dataset_setting.value != DATASET_VERSION:
            _sync_foods(db, DATA_DIR / "foods.csv")
            _sync_recipes(db, DATA_DIR / "recipes.json")
            sync_recipe_classification_metadata(db)
            sync_food_source_metadata(db)
        else:
            sync_recipe_classification_metadata(db)
            sync_food_source_metadata(db)

        if dataset_setting is None:
            db.add(SystemSetting(key="dataset_version", value=DATASET_VERSION, description="Versi dataset bawaan GiziKos."))
        else:
            dataset_setting.value = DATASET_VERSION
        db.commit()

    for key, (value, description) in DEFAULT_SETTINGS.items():
        setting = db.get(SystemSetting, key)
        if setting is None:
            db.add(SystemSetting(key=key, value=value, description=description))
        else:
            setting.description = description
    db.commit()


def _sync_foods(db: Session, path: Path) -> None:
    existing = {item.slug: item for item in db.scalars(select(Food)).all()}
    seen: set[str] = set()
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            slug = row["slug"]
            seen.add(slug)
            food = existing.get(slug) or Food(slug=slug)
            food.name = row["name"]
            food.category = row["category"]
            food.energy = float(row["energy"])
            food.protein = float(row["protein"])
            food.fat = float(row["fat"])
            food.carbs = float(row["carbs"])
            food.fiber = float(row["fiber"])

            if food.id is None:
                food.price_per_100g = float(row["price_per_100g"])

            food.allergens = row["allergens"]
            food.tags = row["tags"]
            if food.id is None:
                food.active = True

            food.source_name = "Estimasi GiziKos"
            food.source_reference = "demo"
            food.verification_status = "demo"
            food.food_state = "processed" if row["category"] == "bumbu" else "raw"
            food.data_year = 2026
            food.price_location = "Estimasi"
            food.price_date = "Estimasi"
            food.portion_reference = "100g"

            db.add(food)
    for slug, food in existing.items():
        if slug not in seen:
            food.active = False
    db.commit()


def _sync_recipes(db: Session, path: Path) -> None:
    foods = {food.slug: food for food in db.scalars(select(Food)).all()}
    existing = {item.slug: item for item in db.scalars(select(Recipe)).all()}
    data = json.loads(path.read_text(encoding="utf-8"))
    seen: set[str] = set()

    for item in data:
        slug = item["id"]
        seen.add(slug)
        recipe = existing.get(slug)
        if recipe is None:
            recipe = Recipe(
                slug=slug,
                name=item["name"],
                meal_type=item["meal_type"],
                cooking_time=int(item["cooking_time"]),
                tools=";".join(item.get("tools", [])),
                tags=";".join(item.get("tags", [])),
                steps_json=json.dumps(item.get("steps", []), ensure_ascii=False),
                active=True,
            )
            db.add(recipe)
            db.flush()
        else:
            recipe.name = item["name"]
            recipe.meal_type = item["meal_type"]
            recipe.cooking_time = int(item["cooking_time"])
            recipe.tools = ";".join(item.get("tools", []))
            recipe.tags = ";".join(item.get("tags", []))
            recipe.steps_json = json.dumps(item.get("steps", []), ensure_ascii=False)

        recipe.source_name = "Rancangan Menu GiziKos"
        recipe.source_reference = "demo"
        recipe.verification_status = "demo"
        recipe.data_year = 2026
        recipe.price_location = "Estimasi"
        recipe.price_date = "Estimasi"
        recipe.food_state = "cooked"
        recipe.portion_reference = "1 porsi"

        recipe.ingredients.clear()
        db.flush()

        main_prot = None
        main_prot_grams = 0.0
        main_carb = None
        main_carb_grams = 0.0
        veg_list = []

        for food_slug, grams in item["ingredients"]:
            food = foods.get(food_slug)
            if food is None:
                raise ValueError(f"Bahan resep tidak ditemukan: {food_slug}")
            recipe.ingredients.append(RecipeIngredient(food_id=food.id, grams=float(grams)))

            if food.category == "protein" and float(grams) > main_prot_grams:
                main_prot = food.slug
                main_prot_grams = float(grams)
            elif food.category == "pokok" and float(grams) > main_carb_grams:
                main_carb = food.slug
                main_carb_grams = float(grams)
            elif food.category == "sayur":
                veg_list.append(food.slug)

        recipe.main_protein = main_prot
        recipe.main_carbohydrate = main_carb
        recipe.vegetable_group = ";".join(sorted(veg_list)) if veg_list else "none"

    for slug, recipe in existing.items():
        if slug not in seen:
            recipe.active = False
    db.commit()


if __name__ == "__main__":
    from .database import SessionLocal

    with SessionLocal() as session:
        seed_database(session)
        print("Database GiziKos berhasil disiapkan dan disinkronkan.")
