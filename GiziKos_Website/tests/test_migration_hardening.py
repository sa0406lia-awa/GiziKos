from __future__ import annotations

import os
import tempfile
from pathlib import Path
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker, selectinload
from sqlalchemy import create_engine

from app.database import (
    Base,
    detect_metadata_sync_requirements,
    validate_recipe_classification,
    initialize_or_migrate_database,
    upgrade_schema,
)
from app.models import Food, Recipe, RecipeIngredient, SystemSetting, User, Consultation
from app.seed import seed_database


@pytest.fixture
def temp_db_env():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    
    orig_env = os.environ.get("GIZIKOS_DB_PATH")
    os.environ["GIZIKOS_DB_PATH"] = path
    
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    
    import app.database
    import app.seed
    import app.config
    orig_engine = app.database.engine
    orig_sessionlocal = app.database.SessionLocal
    orig_seed_engine = app.seed.engine
    orig_db_path = app.config.DB_PATH
    
    app.database.engine = engine
    app.database.SessionLocal = SessionLocal
    app.seed.engine = engine
    app.config.DB_PATH = Path(path)
    
    Base.metadata.create_all(bind=engine)
    
    yield SessionLocal, Path(path)
    
    engine.dispose()
    app.database.engine = orig_engine
    app.database.SessionLocal = orig_sessionlocal
    app.seed.engine = orig_seed_engine
    app.config.DB_PATH = orig_db_path
    
    if orig_env:
        os.environ["GIZIKOS_DB_PATH"] = orig_env
    else:
        os.environ.pop("GIZIKOS_DB_PATH", None)
        
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass


def test_recipe_metadata_empty_fields_detection(temp_db_env):
    SessionLocal, _ = temp_db_env
    fields_to_test = ["data_year", "price_location", "price_date", "food_state", "portion_reference"]
    
    for field in fields_to_test:
        with SessionLocal() as db:
            seed_database(db, force=True)
            db.execute(text(f"UPDATE recipes SET {field} = NULL WHERE id = 1"))
            db.commit()
            
            plan = detect_metadata_sync_requirements(db)
            assert plan.requires_recipe_sync is True
            assert plan.needs_migration is True


def test_food_metadata_empty_fields_detection(temp_db_env):
    SessionLocal, _ = temp_db_env
    fields_to_test = ["source_name", "source_reference", "verification_status", "data_year", "price_location", "price_date", "food_state", "portion_reference"]
    
    for field in fields_to_test:
        with SessionLocal() as db:
            seed_database(db, force=True)
            if field == "data_year":
                db.execute(text("UPDATE foods SET data_year = NULL WHERE id = 1"))
            else:
                db.execute(text(f"UPDATE foods SET {field} = '' WHERE id = 1"))
            db.commit()
            
            plan = detect_metadata_sync_requirements(db)
            assert plan.requires_food_sync is True
            assert plan.needs_migration is True


def test_dataset_version_latest_but_metadata_incomplete(temp_db_env):
    SessionLocal, _ = temp_db_env
    with SessionLocal() as db:
        seed_database(db, force=True)
        db.execute(text("UPDATE recipes SET price_location = NULL WHERE id = 2"))
        db.commit()
        
        plan = detect_metadata_sync_requirements(db)
        assert plan.requires_recipe_sync is True


def test_invalid_metadata_classification_detection(temp_db_env):
    SessionLocal, _ = temp_db_env
    
    # 1. main_protein invalid slug
    with SessionLocal() as db:
        seed_database(db, force=True)
        db.execute(text("UPDATE recipes SET main_protein = 'definitely-invalid-slug' WHERE id = 1"))
        db.commit()
        plan = detect_metadata_sync_requirements(db)
        assert plan.requires_recipe_sync is True

    # 2. main_protein points to non-protein ingredient
    with SessionLocal() as db:
        seed_database(db, force=True)
        # Set main_protein to a carb food like 'nasi-putih'
        db.execute(text("UPDATE recipes SET main_protein = 'nasi-putih' WHERE id = 1"))
        db.commit()
        plan = detect_metadata_sync_requirements(db)
        assert plan.requires_recipe_sync is True

    # 3. main_carbohydrate not in recipe
    with SessionLocal() as db:
        seed_database(db, force=True)
        db.execute(text("UPDATE recipes SET main_carbohydrate = 'kentang' WHERE id = 1"))
        db.commit()
        plan = detect_metadata_sync_requirements(db)
        assert plan.requires_recipe_sync is True

    # 4. recipe has vegetable but vegetable_group = 'none'
    with SessionLocal() as db:
        seed_database(db, force=True)
        # Find a recipe with vegetables
        recipes = db.scalars(select(Recipe).options(selectinload(Recipe.ingredients).joinedload(RecipeIngredient.food))).all()
        veg_recipe = next(r for r in recipes if any(ing.food.category == "sayur" for ing in r.ingredients))
        db.execute(text(f"UPDATE recipes SET vegetable_group = 'none' WHERE id = {veg_recipe.id}"))
        db.commit()
        plan = detect_metadata_sync_requirements(db)
        assert plan.requires_recipe_sync is True


def test_initialize_or_migrate_coordinator(temp_db_env):
    SessionLocal, db_path = temp_db_env
    
    # Add dummy application data
    with SessionLocal() as db:
        seed_database(db, force=True)
        db.add(User(id="u-test", name="Tester", email="test@example.com", password_hash="hash", active=True))
        db.add(Consultation(id="c-test", browser_id="b1", user_id="u-test", name="Tester", input_json="{}", result_json="{}"))
        db.execute(text("UPDATE recipes SET price_location = NULL WHERE id = 1"))
        db.commit()
        
    res = initialize_or_migrate_database()
    assert res.status == "success"
    assert res.backup_created is True
    assert res.backup_path is not None
    assert os.path.exists(res.backup_path)
    
    # Check application data preserved
    with SessionLocal() as db:
        assert db.get(User, "u-test") is not None
        assert db.get(Consultation, "c-test") is not None
        r = db.get(Recipe, 1)
        assert r.price_location == "Estimasi"

    # Second run idempotent: no new backup created
    res2 = initialize_or_migrate_database()
    assert res2.backup_created is False
