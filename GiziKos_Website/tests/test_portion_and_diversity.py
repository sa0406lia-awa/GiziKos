from __future__ import annotations

import os
import tempfile
from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.seed import seed_database
from app.schemas import ConsultationInput
from app.services import generate_recommendation, calculate_totals


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
    
    yield SessionLocal
    
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


def test_portion_optimization_integration_proof(temp_db_env):
    SessionLocal = temp_db_env
    with SessionLocal() as db:
        seed_database(db, force=True)
        
        user = ConsultationInput(
            name="PortionUser", age=21, gender="male", weight=60, height=165, activity_level="medium",
            goal="balanced", daily_budget=60000, meal_count=3, max_cooking_time=45, tools=["kompor", "wajan", "panci", "magic-com"],
            allergies=[], vegetarian=False, disliked_ingredients=[], available_ingredients=["telur", "nasi-putih"],
            has_medical_condition=False, consent=True
        )
        
        res1 = generate_recommendation(db, user, "browser-p1")
        res2 = generate_recommendation(db, user, "browser-p2")
        
        assert res1["status"] in ("complete", "partial")

        # Check output determinism
        assert [m["id"] for m in res1["plan"]] == [m["id"] for m in res2["plan"]]
        assert [m["portion_multiplier"] for m in res1["plan"]] == [m["portion_multiplier"] for m in res2["plan"]]
        
        # Check ingredient quantities and nutrition match multiplier
        for item in res1["plan"]:
            mult = item["portion_multiplier"]
            assert mult in [0.75, 1.0, 1.25, 1.5]
            for ing in item["ingredients"]:
                # Grams should be scaled by multiplier
                assert ing["grams"] > 0


def test_diversity_integration_scenarios_proof(temp_db_env):
    SessionLocal = temp_db_env
    with SessionLocal() as db:
        seed_database(db, force=True)
        
        # Scenario A: Diverse alternatives available
        user_diverse = ConsultationInput(
            name="DiverseUser", age=22, gender="female", weight=54, height=162, activity_level="medium",
            goal="balanced", daily_budget=65000, meal_count=3, max_cooking_time=45, tools=["kompor", "wajan", "panci", "magic-com"],
            allergies=[], vegetarian=False, disliked_ingredients=[], available_ingredients=["telur", "tempe", "ayam-dada"],
            has_medical_condition=False, consent=True
        )
        res_a = generate_recommendation(db, user_diverse, "browser-div-a")
        analysis_a = res_a["combination_analysis"]
        assert analysis_a["unique_protein_count"] >= 2
        assert res_a["status"] == "complete"
        
        # Scenario B: Limited options -> variation warning true
        user_limited = ConsultationInput(
            name="LimitedUser", age=20, gender="female", weight=48, height=155, activity_level="low",
            goal="hemat", daily_budget=25000, meal_count=3, max_cooking_time=20, tools=["kompor", "wajan"],
            allergies=[], vegetarian=True, disliked_ingredients=["tahu", "kacang-tanah", "bayam"], available_ingredients=["tempe"],
            has_medical_condition=False, consent=True
        )
        res_b = generate_recommendation(db, user_limited, "browser-div-b")
        analysis_b = res_b["combination_analysis"]
        assert analysis_b["variation_warning"] is True
        assert analysis_b["variation_message"] is not None
        
        # Scenario C: Hard constraint beats diversity
        user_allergy = ConsultationInput(
            name="AllergyUser", age=22, gender="male", weight=65, height=170, activity_level="medium",
            goal="balanced", daily_budget=55000, meal_count=3, max_cooking_time=30, tools=["kompor", "wajan"],
            allergies=["udang", "egg", "fish"], vegetarian=False, disliked_ingredients=[], available_ingredients=[],
            has_medical_condition=False, consent=True
        )
        res_c = generate_recommendation(db, user_allergy, "browser-div-c")
        for item in res_c["plan"]:
            for ing in item["ingredients"]:
                assert ing["slug"] not in ("udang", "telur", "ikan-tongkol", "ikan-lele")


def test_alternatives_deduplication(temp_db_env):
    SessionLocal = temp_db_env
    with SessionLocal() as db:
        seed_database(db, force=True)
        user = ConsultationInput(
            name="AltUser", age=22, gender="male", weight=60, height=165, activity_level="medium",
            goal="balanced", daily_budget=60000, meal_count=3, max_cooking_time=45, tools=["kompor", "wajan", "panci", "magic-com"],
            allergies=[], vegetarian=False, disliked_ingredients=[], available_ingredients=[],
            has_medical_condition=False, consent=True
        )
        res = generate_recommendation(db, user, "browser-alt")
        assert res["status"] in ("complete", "partial")
        for meal_type, alt_list in res["alternatives"].items():
            slugs = [item["id"] for item in alt_list]
            assert len(slugs) == len(set(slugs)), f"Duplicate recipe slug found in alternatives for {meal_type}: {slugs}"

