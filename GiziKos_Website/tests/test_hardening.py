from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import APP_VERSION, SECRET_KEY, ADMIN_PASSWORD
from app.database import Base, engine, SessionLocal, upgrade_schema
from app.expert_system import (
    evaluate_daily_combinations,
    goal_compatibility_score,
    recipe_metrics,
    run_forward_chaining,
    estimate_energy_target,
    meal_split
)
from app.main import app, RATE_LIMIT_DATA
from app.models import Food, Recipe, SystemSetting, User, Consultation
from app.seed import seed_database
from app.schemas import ConsultationInput


@pytest.fixture
def clean_db():
    # Setup temporary database for testing
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    
    orig_env = os.environ.get("GIZIKOS_DB_PATH")
    os.environ["GIZIKOS_DB_PATH"] = path
    
    # Recreate engine for temp db
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    temp_engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    TempSessionLocal = sessionmaker(bind=temp_engine, autoflush=False, autocommit=False)
    
    Base.metadata.create_all(bind=temp_engine)
    
    yield TempSessionLocal
    
    temp_engine.dispose()
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass
            
    if orig_env:
        os.environ["GIZIKOS_DB_PATH"] = orig_env


def test_app_version():
    assert APP_VERSION == "3.2.3"



def test_database_creation_on_startup(clean_db):
    with clean_db() as db:
        # Run upgrade and seeding explicitly
        upgrade_schema()
        seed_database(db, force=True)
        
        # Check settings
        dataset_version = db.get(SystemSetting, "dataset_version")
        assert dataset_version is not None
        assert dataset_version.value == "2.2"
        
        # Check foods and recipes counts
        foods_count = db.scalar(select(Food.id).limit(1))
        recipes_count = db.scalar(select(Recipe.id).limit(1))
        assert foods_count is not None
        assert recipes_count is not None


def test_repetition_penalty_deduction():
    # Verify repetition penalty is only subtracted once (Option A)
    # Using evaluate_daily_combinations
    # We construct a mock combination with high repetitions
    # e.g., three meals having the same main protein
    
    class MockRecipe:
        def __init__(self, main_protein, main_carbohydrate, vegetable_group, name="mock"):
            self.id = 1
            self.slug = "mock"
            self.name = name
            self.meal_type = "lunch"
            self.cooking_time = 15
            self.tools = "kompor"
            self.tags = "halal;vegetarian"
            self.steps_json = "[]"
            self.active = True
            self.main_protein = main_protein
            self.main_carbohydrate = main_carbohydrate
            self.vegetable_group = vegetable_group
            self.ingredients = []

    class MockCandidateResult:
        def __init__(self, recipe):
            self.recipe = recipe
            self.score = 80.0
            self.metrics = {"energy": 600.0, "protein": 20.0, "fat": 15.0, "carbs": 80.0, "fiber": 5.0, "cost": 12000.0}

    # Combo 1: All same main protein (tempe) -> high penalty
    r1 = MockRecipe("tempe", "nasi-putih", "bayam")
    c1 = MockCandidateResult(r1)
    combo_repeated = [c1, c1, c1]
    
    # Combo 2: Different main proteins -> no penalty
    r_diff1 = MockRecipe("tempe", "nasi-putih", "bayam", "r1")
    r_diff2 = MockRecipe("telur", "nasi-putih", "wortel", "r2")
    r_diff3 = MockRecipe("ayam-dada", "nasi-putih", "kol", "r3")
    combo_diverse = [MockCandidateResult(r_diff1), MockCandidateResult(r_diff2), MockCandidateResult(r_diff3)]
    
    user = ConsultationInput(
        name="Tester", age=21, gender="male", weight=60, height=165, activity_level="medium",
        goal="balanced", daily_budget=50000, meal_count=3, max_cooking_time=30, tools=["kompor"],
        allergies=[], vegetarian=False, disliked_ingredients=[], available_ingredients=["tempe", "nasi-putih"],
        has_medical_condition=False, consent=True
    )
    
    weights = {"fuzzy": 0.30, "energy_adequacy": 0.20, "protein_adequacy": 0.15, "budget": 0.10, "stock": 0.10, "goal": 0.10, "diversity": 0.05}
    
    res_repeated = evaluate_daily_combinations(combo_repeated, user, {}, weights)
    res_diverse = evaluate_daily_combinations(combo_diverse, user, {}, weights)
    
    # Diverse combo must score better than repeated combo
    assert res_diverse["score"] > res_repeated["score"]
    
    # Repetition penalty should be logged
    assert res_repeated["total_repetition_penalty"] > 0
    assert res_repeated["protein_repetition_penalty"] > 0
    
    # Check that combination score is exactly the weighted score (Option A)
    # i.e. diversity_score was penalized, but combination_score is not further reduced by repetition_penalty
    expected_repeated_weighted = (
        weights["fuzzy"] * res_repeated["avg_fuzzy"] +
        weights["energy_adequacy"] * res_repeated["energy_adequacy"] +
        weights["protein_adequacy"] * res_repeated["protein_adequacy"] +
        weights["budget"] * res_repeated["budget_score"] +
        weights["stock"] * res_repeated["stock_score"] +
        weights["goal"] * res_repeated["avg_goal"] +
        weights["diversity"] * res_repeated["diversity_score"]
    )
    assert abs(res_repeated["score"] - expected_repeated_weighted) < 0.2


def test_goal_compatibility_score_lose_proximity():
    # Lose goal energy compatibility should peak near target deficite, not reward starve calories
    user = ConsultationInput(
        name="Tester", age=21, gender="male", weight=60, height=165, activity_level="medium",
        goal="lose", daily_budget=50000, meal_count=3, max_cooking_time=30, tools=["kompor"],
        allergies=[], vegetarian=False, disliked_ingredients=[], available_ingredients=[],
        has_medical_condition=False, consent=True
    )
    
    target_daily = estimate_energy_target(user)["target"]
    
    # Candidate metrics with different calorie contents (representing 1 meal)
    # Target per meal: target_daily * meal_split
    target_meal = target_daily * meal_split(user, "lunch")
    
    # High fitness candidate (ratio ~0.95)
    m_fit = {"energy": target_meal * 0.95, "protein": 22.0, "fat": 15.0, "carbs": 80.0, "fiber": 7.0}
    # Too low calorie candidate (ratio ~0.50)
    m_low = {"energy": target_meal * 0.50, "protein": 22.0, "fat": 15.0, "carbs": 80.0, "fiber": 7.0}
    # Too high calorie candidate (ratio ~1.30)
    m_high = {"energy": target_meal * 1.30, "protein": 22.0, "fat": 15.0, "carbs": 80.0, "fiber": 7.0}
    
    score_fit = goal_compatibility_score(m_fit, user, "lunch", target_daily)
    score_low = goal_compatibility_score(m_low, user, "lunch", target_daily)
    score_high = goal_compatibility_score(m_high, user, "lunch", target_daily)
    
    # The meal closer to target (fit) must score better than the starving calorie meal (low)
    assert score_fit > score_low
    # The meal closer to target (fit) must score better than high calorie meal (high)
    assert score_fit > score_high


def test_portion_scaling_metrics():
    # Mock food and recipe with ingredients
    # We test that recipe_metrics scales cost and nutrition with portion multiplier
    
    class MockFood:
        def __init__(self):
            self.energy = 100.0
            self.protein = 10.0
            self.fat = 5.0
            self.carbs = 20.0
            self.fiber = 2.0
            self.price_per_100g = 1000.0
            self.allergens = ""
            self.tags = ""
            self.slug = "food-mock"

    class MockIngredient:
        def __init__(self):
            self.grams = 100.0
            self.food = MockFood()

    class MockRecipe:
        def __init__(self):
            self.ingredients = [MockIngredient()]
            self.meal_type = "lunch"
            
    r = MockRecipe()
    metrics_1x = recipe_metrics(r, 1.0)
    metrics_1_5x = recipe_metrics(r, 1.5)
    metrics_0_75x = recipe_metrics(r, 0.75)
    
    assert metrics_1x["energy"] == 100.0
    assert metrics_1_5x["energy"] == 150.0
    assert metrics_0_75x["energy"] == 75.0
    
    assert metrics_1x["cost"] == 1000.0
    assert metrics_1_5x["cost"] == 1500.0
    assert metrics_0_75x["cost"] == 750.0


def test_admin_login_rate_limiting():
    RATE_LIMIT_DATA.clear()
    with TestClient(app) as client:
        # Get csrf token first
        csrf = ""
        response = client.get("/admin")
        import re
        match = re.search(r'name="csrf_token"\s+value="([^"]+)"', response.text)
        if match:
            csrf = match.group(1)
            
        # Try to POST login multiple times to trigger rate limit (5 allowed in 60s)
        for i in range(10):
            res = client.post("/admin/login", data={"password": "wrong-password", "csrf_token": csrf})
            if res.status_code == 429:
                break
        else:
            pytest.fail("Rate limiter on /admin/login did not trigger!")


def test_production_environment_validations(monkeypatch):
    monkeypatch.setenv("GIZIKOS_ENV", "production")
    
    # Test weak SECRET_KEY triggers value error
    monkeypatch.setenv("GIZIKOS_SECRET_KEY", "short")
    monkeypatch.setenv("GIZIKOS_ADMIN_PASSWORD", "strong_admin_pass_123")
    
    with pytest.raises(ValueError) as excinfo:
        import importlib
        import app.config
        importlib.reload(app.config)
    assert "SECRET_KEY" in str(excinfo.value)
    
    # Test weak ADMIN_PASSWORD triggers value error
    monkeypatch.setenv("GIZIKOS_SECRET_KEY", "extremely_long_secure_secret_key_123456")
    monkeypatch.setenv("GIZIKOS_ADMIN_PASSWORD", "gizikos-admin")
    
    with pytest.raises(ValueError) as excinfo:
        importlib.reload(app.config)
    assert "ADMIN_PASSWORD" in str(excinfo.value)
    
    # Restore env for rest of tests
    monkeypatch.setenv("GIZIKOS_ENV", "testing")
    importlib.reload(app.config)


def test_zip_packaging_hygiene(tmp_path):
    from scripts.build_release import build_release, BASE_DIR
    res = build_release(source_dir=BASE_DIR, output_dir=tmp_path)
    assert res.success is True
    assert res.zip_path is not None and res.zip_path.exists()
    assert res.checksum_path is not None and res.checksum_path.exists()

    with zipfile.ZipFile(res.zip_path, "r") as zipf:
        namelist = zipf.namelist()
        
        # Verify checksum is NOT inside ZIP
        assert "GiziKos_Website_Final_Production.zip.sha256" not in namelist
        
        # Verify no .venv, .env, or database
        for name in namelist:
            parts = Path(name).parts
            assert ".venv" not in parts
            assert "venv" not in parts
            assert "__pycache__" not in parts
            assert ".pytest_cache" not in parts
            assert ".git" not in parts
            
            filename = Path(name).name
            assert filename != "gizikos.db"
            assert filename != ".env"
            assert not filename.endswith(".sqlite")
            assert not filename.endswith(".sqlite3")
            
        # Verify essential files
        assert "start.bat" in namelist
        assert "run.py" in namelist
        assert "requirements.txt" in namelist
        assert "pyproject.toml" in namelist
        assert "data/foods.csv" in namelist
        assert "data/recipes.json" in namelist
        assert "app/main.py" in namelist


def test_root_release_artifacts_integrity(tmp_path):
    from scripts.build_release import BASE_DIR, build_release, calculate_sha256
    zip_root = BASE_DIR / "GiziKos_Website_Final_Production.zip"
    checksum_root = BASE_DIR / "GiziKos_Website_Final_Production.zip.sha256"
    
    hash_before = calculate_sha256(zip_root) if zip_root.exists() else None
    
    # Run build inside tmp_path
    res = build_release(source_dir=BASE_DIR, output_dir=tmp_path)
    assert res.success is True
    
    hash_after = calculate_sha256(zip_root) if zip_root.exists() else None
    assert hash_before == hash_after



def test_database_migration_idempotent():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    
    # 1. Create a simulated "old" 2.0 database
    conn = sqlite3.connect(path)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, name VARCHAR(120), email VARCHAR(255), password_hash VARCHAR(600), active BOOLEAN, created_at DATETIME);")
    cursor.execute("CREATE TABLE consultations (id VARCHAR(36) PRIMARY KEY, browser_id VARCHAR(36), name VARCHAR(120), input_json TEXT, result_json TEXT, created_at DATETIME);")
    cursor.execute("CREATE TABLE foods (id INTEGER PRIMARY KEY, slug VARCHAR(120), name VARCHAR(160), category VARCHAR(60), energy FLOAT, protein FLOAT, fat FLOAT, carbs FLOAT, fiber FLOAT, price_per_100g FLOAT, allergens VARCHAR(200), tags VARCHAR(300), active BOOLEAN, created_at DATETIME);")
    cursor.execute("CREATE TABLE recipes (id INTEGER PRIMARY KEY, slug VARCHAR(160), name VARCHAR(200), meal_type VARCHAR(30), cooking_time INTEGER, tools VARCHAR(300), tags VARCHAR(300), steps_json TEXT, active BOOLEAN, created_at DATETIME);")
    
    # Insert mock user and consultation
    cursor.execute("INSERT INTO users VALUES ('user-123', 'Old User', 'old@example.com', 'hash', 1, '2026-06-27 12:00:00');")
    cursor.execute("INSERT INTO consultations VALUES ('consult-456', 'browser-123', 'Old User', '{}', '{}', '2026-06-27 12:00:00');")
    
    # Insert mock foods and recipes matching actual slug to verify seed
    cursor.execute("INSERT INTO foods (id, slug, name, category, energy, protein, fat, carbs, fiber, price_per_100g, allergens, tags, active) VALUES (1, 'nasi-putih', 'Nasi putih', 'pokok', 175, 3.2, 0.3, 40.6, 0.3, 1800, '', 'halal;vegetarian', 1);")
    cursor.execute("INSERT INTO recipes (id, slug, name, meal_type, cooking_time, tools, tags, steps_json, active) VALUES (1, 'sarapan-nasi-telur-bayam', 'Nasi Telur Rebus & Tumis Bayam', 'breakfast', 18, 'kompor;panci;wajan', 'hemat', '[]', 1);")
    
    conn.commit()
    conn.close()
    
    # 2. Point app database path to it
    orig_env = os.environ.get("GIZIKOS_DB_PATH")
    os.environ["GIZIKOS_DB_PATH"] = path
    
    # Recreate engine and session for migration
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    temp_engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    TempSessionLocal = sessionmaker(bind=temp_engine, autoflush=False, autocommit=False)
    
    # Create missing tables (like system_settings and recipe_ingredients) first, mimicking app lifespan
    Base.metadata.create_all(bind=temp_engine)
    
    # We must patch app.database.engine and app.seed.engine to use temp_engine
    import app.database
    import app.seed
    orig_engine = app.database.engine
    orig_seed_engine = app.seed.engine
    app.database.engine = temp_engine
    app.seed.engine = temp_engine
    
    try:
        # Run upgrade schema & seed database
        upgrade_schema()
        with TempSessionLocal() as db:
            seed_database(db)
            
        # Verify columns added & filled
        conn = sqlite3.connect(path)
        cursor = conn.cursor()
        
        # Check user_id in consultations
        cursor.execute("PRAGMA table_info(consultations);")
        cols = [c[1] for c in cursor.fetchall()]
        assert "user_id" in cols
        
        # Check new cols in recipes
        cursor.execute("PRAGMA table_info(recipes);")
        recipe_cols = [c[1] for c in cursor.fetchall()]
        assert "main_protein" in recipe_cols
        assert "main_carbohydrate" in recipe_cols
        assert "vegetable_group" in recipe_cols
        
        # Check that value was populated by seed
        cursor.execute("SELECT main_protein, main_carbohydrate FROM recipes WHERE slug = 'sarapan-nasi-telur-bayam';")
        row = cursor.fetchone()
        assert row is not None
        assert row[0] is not None
        
        # Check that user and consultations still exist
        cursor.execute("SELECT COUNT(*) FROM users;")
        assert cursor.fetchone()[0] == 2
        cursor.execute("SELECT COUNT(*) FROM consultations;")
        assert cursor.fetchone()[0] == 1
        
        conn.close()
        
        # 3. Second run should be idempotent and not fail
        upgrade_schema()
        with TempSessionLocal() as db:
            seed_database(db)
            
    finally:
        temp_engine.dispose()
        app.database.engine = orig_engine
        app.seed.engine = orig_seed_engine
        if orig_env:
            os.environ["GIZIKOS_DB_PATH"] = orig_env
        else:
            del os.environ["GIZIKOS_DB_PATH"]
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
