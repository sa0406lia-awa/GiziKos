from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path
import pytest
from sqlalchemy import select, text
from fastapi.testclient import TestClient

from app.config import APP_VERSION, COMBINATION_WEIGHTS
from app.database import Base, upgrade_schema, database_has_existing_application_data
from app.seed import seed_database, sync_recipe_classification_metadata, sync_food_source_metadata
from app.models import Food, Recipe, SystemSetting, User, Consultation, RecipeIngredient
from app.schemas import ConsultationInput
from app.services import (
    generate_recommendation,
    serialize_consultation_for_user,
    serialize_consultation_for_admin,
    is_better_combination
)
from app.main import app


@pytest.fixture
def temp_db():
    # Setup temporary database
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    
    orig_env = os.environ.get("GIZIKOS_DB_PATH")
    os.environ["GIZIKOS_DB_PATH"] = path
    
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    temp_engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    TempSessionLocal = sessionmaker(bind=temp_engine, autoflush=False, autocommit=False)
    
    # Patch database engine in app modules
    import app.database
    import app.seed
    import app.config
    orig_engine = app.database.engine
    orig_seed_engine = app.seed.engine
    orig_db_path = app.config.DB_PATH
    
    app.database.engine = temp_engine
    app.seed.engine = temp_engine
    app.config.DB_PATH = Path(path)
    
    yield TempSessionLocal, Path(path)
    
    temp_engine.dispose()
    app.database.engine = orig_engine
    app.seed.engine = orig_seed_engine
    app.config.DB_PATH = orig_db_path
    
    if orig_env:
        os.environ["GIZIKOS_DB_PATH"] = orig_env
    else:
        del os.environ["GIZIKOS_DB_PATH"]
        
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass
            
    # Clean backups folder if exists
    backups_dir = Path(path).parent / "backups"
    if backups_dir.exists():
        shutil.rmtree(backups_dir, ignore_errors=True)


@pytest.fixture
def clean_db(temp_db):
    TempSessionLocal, _ = temp_db
    # Make sure tables are created
    import app.database
    Base.metadata.create_all(bind=app.database.engine)
    yield TempSessionLocal


def test_migration_metadata_null(temp_db):
    TempSessionLocal, db_path = temp_db
    
    # 1. Create correct tables with all columns using SQLAlchemy
    import app.database
    Base.metadata.create_all(bind=app.database.engine)
    
    # 2. Insert mock user, consultation, foods, and recipes using ORM or raw SQL
    with TempSessionLocal() as db:
        db.add(User(id="u1", name="Galang", email="galang@example.com", password_hash="pwd", active=True))
        db.add(Consultation(id="c1", browser_id="b1", user_id="u1", name="Galang", input_json="{}", result_json="{}"))
        
        # Insert food and recipe with NULL / empty metadata
        # (verification_status, source_name, source_reference will default to NULL since they are not set)
        food = Food(id=1, slug="telur", name="Telur", category="protein", energy=150, protein=12, fat=10, carbs=1, fiber=0, price_per_100g=1500, active=True)
        recipe = Recipe(id=1, slug="telur-rebus", name="Telur Rebus", meal_type="breakfast", cooking_time=10, active=True)
        db.add(food)
        db.add(recipe)
        db.commit()
        
        # Add ingredient
        db.add(RecipeIngredient(id=1, recipe_id=1, food_id=1, grams=50.0))
        # Add system version setting to 2.2
        db.add(SystemSetting(key="dataset_version", value="2.2", description="Versi dataset bawaan GiziKos."))
        db.commit()
        
    # Run schema upgrade and seed
    upgrade_schema()
    with TempSessionLocal() as db:
        seed_database(db)
        
        # Verify that user and consultation are preserved
        assert db.scalar(select(User.id).where(User.id == "u1")) is not None
        assert db.scalar(select(Consultation.id).where(Consultation.id == "c1")) is not None
        
        # Verify metadata is updated/synced
        recipe = db.get(Recipe, 1)
        assert recipe.main_protein == "telur"
        assert recipe.verification_status == "demo"
        assert recipe.source_name == "Rancangan Menu GiziKos"
        
        food = db.get(Food, 1)
        assert food.verification_status == "demo"
        assert food.source_name == "Estimasi GiziKos"
        
        # Second run - check idempotency
        seed_database(db)
        assert db.scalar(select(User.id).where(User.id == "u1")) is not None
        assert recipe.main_protein == "telur"


def test_fresh_install_no_backup(temp_db):
    TempSessionLocal, db_path = temp_db
    
    # 1. Fresh Install - Database tables are initialized empty
    import app.database
    Base.metadata.create_all(bind=app.database.engine)
    
    with TempSessionLocal() as db:
        has_data = database_has_existing_application_data(db)
        assert has_data is False
        
    # Run upgrade_schema
    upgrade_schema()
    
    # Check that NO backup directory or files exist
    backups_dir = db_path.parent / "backups"
    assert not backups_dir.exists() or len(list(backups_dir.glob("*.db"))) == 0
    
    # 2. Database with data - Seed and add user
    with TempSessionLocal() as db:
        seed_database(db)
        db.add(User(id="tester", name="Tester", email="tester@example.com", password_hash="pwd", active=True))
        db.commit()
        
    # Check that database HAS existing application data
    with TempSessionLocal() as db:
        has_data = database_has_existing_application_data(db)
        assert has_data is True
        
    # Run upgrade_schema again, but this time we delete the 'dataset_version' setting to force migration check
    with TempSessionLocal() as db:
        db.execute(text("DELETE FROM system_settings WHERE key = 'dataset_version'"))
        db.commit()
        
    upgrade_schema()
    
    # Verify backup is created because data existed
    assert backups_dir.exists()
    backup_files = list(backups_dir.glob("gizikos_before_3_2_*.db"))
    assert len(backup_files) > 0


def test_backup_location_and_integrity(temp_db):
    TempSessionLocal, db_path = temp_db
    
    # Move database path to a subfolder
    custom_dir = db_path.parent / "custom_data_dir"
    custom_dir.mkdir(exist_ok=True)
    custom_db_path = custom_dir / "my_gizikos.db"
    
    # Copy current temp database to custom path
    shutil.copy2(db_path, custom_db_path)
    
    # Patch config & database DB_PATH and reload engine
    import app.config
    import app.database
    import app.seed
    from sqlalchemy import create_engine
    
    app.config.DB_PATH = custom_db_path
    app.database.DB_PATH = custom_db_path
    custom_engine = create_engine(f"sqlite:///{custom_db_path}", connect_args={"check_same_thread": False})
    
    orig_engine = app.database.engine
    orig_seed_engine = app.seed.engine
    app.database.engine = custom_engine
    app.seed.engine = custom_engine
    
    # Insert some data so backup is triggered
    Base.metadata.create_all(bind=custom_engine)
    with TempSessionLocal(bind=custom_engine) as db:
        db.add(User(id="user-1", name="Budi", email="budi@example.com", password_hash="pwd", active=True))
        db.commit()
        
    try:
        # Run upgrade_schema
        upgrade_schema()
        
        # Check that backup is created in custom_dir / backups/
        custom_backups_dir = custom_dir / "backups"
        assert custom_backups_dir.exists()
        backup_files = list(custom_backups_dir.glob("gizikos_before_3_2_*.db"))
        assert len(backup_files) == 1
        
        # Verify backup is a valid SQLite DB
        conn_b = sqlite3.connect(backup_files[0])
        res = conn_b.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users';").fetchone()
        assert res is not None
        conn_b.close()
    finally:
        custom_engine.dispose()
        app.database.engine = orig_engine
        app.seed.engine = orig_seed_engine
        # Cleanup custom folder
        shutil.rmtree(custom_dir, ignore_errors=True)


def test_portion_optimization(clean_db):
    # Using clean_db session local
    with clean_db() as db:
        # Seed default data
        seed_database(db, force=True)
        
        # Create a user with lose goal and budget constraints
        user = ConsultationInput(
            name="Tester", age=22, gender="female", weight=55, height=160, activity_level="medium",
            goal="balanced", daily_budget=45000, meal_count=3, max_cooking_time=45, tools=["kompor", "wajan", "panci"],
            allergies=[], vegetarian=False, disliked_ingredients=[], available_ingredients=["telur", "tempe"],
            has_medical_condition=False, consent=True
        )
        
        # Run recommendations
        result = generate_recommendation(db, user, "browser-1")
        
        # Check that plan has portion scaling multipliers
        assert "plan" in result
        assert len(result["plan"]) > 0
        for item in result["plan"]:
            assert "portion_multiplier" in item
            assert item["portion_multiplier"] in [0.75, 1.0, 1.25, 1.5]
            
            # Verify nutrition & cost follow portion multiplier
            mult = item["portion_multiplier"]
            db_recipe = db.scalar(select(Recipe).where(Recipe.slug == item["id"]))
            
            # Calculate base metrics
            base_metrics = {"energy": 0.0, "cost": 0.0}
            for ing in db_recipe.ingredients:
                factor = ing.grams / 100.0
                base_metrics["energy"] += ing.food.energy * factor
                base_metrics["cost"] += ing.food.price_per_100g * factor
                
            # Verify scaled metrics
            assert abs(item["nutrition"]["energy"] - base_metrics["energy"] * mult) < 2.0
            assert abs(item["nutrition"]["cost"] - base_metrics["cost"] * mult) < 2.0


def test_diversity_weights_and_tie_breaker():
    # Construct two daily combination details
    # Combo A: has protein repetition penalty, but score is slightly higher initially
    combo_a = {
        "score": 85.0,
        "energy_adequacy": 95.0,
        "protein_adequacy": 90.0,
        "budget_score": 90.0,
        "stock_score": 80.0,
        "avg_goal": 80.0,
        "diversity_score": 50.0,
        "protein_repetition_penalty": 15.0,
        "carbohydrate_repetition_penalty": 0.0,
        "vegetable_repetition_penalty": 0.0,
        "method_repetition_penalty": 5.0,
        "total_repetition_penalty": 20.0,
        "unique_protein_count": 2,
        "unique_vegetable_count": 2,
        "total_energy": 1900.0,
        "total_cost": 24000.0
    }
    
    # Combo B: has no repetition penalty, score is within 2 points (e.g. 84.5)
    combo_b = {
        "score": 84.5,
        "energy_adequacy": 94.0,
        "protein_adequacy": 92.0,
        "budget_score": 90.0,
        "stock_score": 80.0,
        "avg_goal": 80.0,
        "diversity_score": 100.0,
        "protein_repetition_penalty": 0.0,
        "carbohydrate_repetition_penalty": 0.0,
        "vegetable_repetition_penalty": 0.0,
        "method_repetition_penalty": 0.0,
        "total_repetition_penalty": 0.0,
        "unique_protein_count": 3,
        "unique_vegetable_count": 3,
        "total_energy": 1880.0,
        "total_cost": 25000.0
    }
    
    # Test that is_better_combination prefers Combo B over Combo A
    # Since scores are within 2 points, Combo B wins due to lower repetition penalty (0.0 < 20.0)
    assert is_better_combination(combo_b, combo_a) is True
    
    # Combo C: score is much higher than Combo B (e.g., 87.0 vs 84.5)
    # Exceeds the 2.0 point tie-breaker threshold, so Combo C wins despite repetition penalty
    combo_c = dict(combo_a)
    combo_c["score"] = 87.0
    assert is_better_combination(combo_b, combo_c) is False


def test_payload_security_serialization(clean_db):
    with clean_db() as db:
        seed_database(db, force=True)
        user = ConsultationInput(
            name="Alice", age=20, gender="female", weight=50, height=158, activity_level="low",
            goal="hemat", daily_budget=35000, meal_count=2, max_cooking_time=30, tools=["kompor", "wajan"],
            allergies=[], vegetarian=False, disliked_ingredients=[], available_ingredients=[],
            has_medical_condition=False, consent=True
        )
        
        result = generate_recommendation(db, user, "browser-alice")
        assert "rejected_candidates_debug" in result
        
        # User serialization
        user_payload = serialize_consultation_for_user(result)
        assert "rejected_candidates_debug" not in user_payload
        assert "combination_analysis" not in user_payload
        assert "combination_analysis_safe" in user_payload
        
        # Safe analysis content
        safe_an = user_payload["combination_analysis_safe"]
        assert "energy_adequacy" not in safe_an
        assert "protein_adequacy" not in safe_an

        assert "diversity_score_label" in safe_an
        assert "variation_warning" in safe_an
        
        # Check rule trace is cleaned from plan items
        for item in user_payload["plan"]:
            assert "fired_rules" not in item
            assert "fuzzy" in item
            assert "memberships" not in item["fuzzy"]
            assert "active_rules" not in item["fuzzy"]
            
        # Admin serialization
        admin_payload = serialize_consultation_for_admin(result)
        assert "rejected_candidates_debug" in admin_payload
        assert "combination_analysis" in admin_payload


def test_checksum_release(tmp_path):
    from scripts.build_release import build_release, ZIP_NAME, BASE_DIR
    
    # Run build release script in tmp_path
    res = build_release(source_dir=BASE_DIR, output_dir=tmp_path)
    assert res.success is True
    
    checksum_path = tmp_path / f"{ZIP_NAME}.sha256"
    zip_path = tmp_path / ZIP_NAME
    assert checksum_path.exists()
    assert zip_path.exists()
    
    # Verify hash content format: <sha256>  GiziKos_Website_Final_Production.zip
    content = checksum_path.read_text(encoding="utf-8").strip()
    parts = content.split("  ")
    assert len(parts) == 2
    assert len(parts[0]) == 64 # SHA-256 is 64 hex characters
    assert parts[1] == ZIP_NAME
    
    # Verify actual hash matches
    import hashlib
    with open(zip_path, "rb") as f:
        sha256_hash = hashlib.sha256(f.read()).hexdigest()
    assert parts[0] == sha256_hash

