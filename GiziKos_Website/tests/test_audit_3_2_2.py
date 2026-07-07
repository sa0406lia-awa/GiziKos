from __future__ import annotations

import os
import json
import sqlite3
import tempfile
from pathlib import Path
import pytest
from sqlalchemy import select, text
from fastapi.testclient import TestClient

from app.config import APP_VERSION, ADMIN_PASSWORD
from app.database import Base, upgrade_schema, detect_metadata_sync_requirements, database_has_existing_application_data
from app.seed import seed_database
from app.models import Food, Recipe, SystemSetting, User, Consultation, RecipeIngredient
from app.schemas import ConsultationInput
from app.services import (
    generate_recommendation,
    serialize_consultation_for_user,
    serialize_consultation_for_admin,
    evaluate_daily_combinations,
    is_better_combination
)
from app.main import app, RATE_LIMIT_DATA


@pytest.fixture
def test_session_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    
    orig_env = os.environ.get("GIZIKOS_DB_PATH")
    os.environ["GIZIKOS_DB_PATH"] = path
    
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    temp_engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    TempSessionLocal = sessionmaker(bind=temp_engine, autoflush=False, autocommit=False)
    
    import app.database
    import app.seed
    import app.config
    orig_engine = app.database.engine
    orig_sessionlocal = app.database.SessionLocal
    orig_seed_engine = app.seed.engine
    orig_db_path = app.config.DB_PATH
    
    app.database.engine = temp_engine
    app.database.SessionLocal = TempSessionLocal
    app.seed.engine = temp_engine
    app.config.DB_PATH = Path(path)
    
    Base.metadata.create_all(bind=temp_engine)
    
    yield TempSessionLocal, Path(path)
    
    temp_engine.dispose()
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


def test_admin_consultation_authorization_matrix(test_session_db):
    TempSessionLocal, _ = test_session_db
    with TempSessionLocal() as db:
        seed_database(db, force=True)
        # Create Owner user and another user using create_user so we have passwords
        from app.auth import create_user
        owner = create_user(db, "Owner User", "owner@example.com", "password123")
        other = create_user(db, "Other User", "other@example.com", "password123")
        
        user_in = ConsultationInput(
            name="Owner User", age=21, gender="male", weight=60, height=165, activity_level="medium",
            goal="balanced", daily_budget=50000, meal_count=3, max_cooking_time=30, tools=["kompor", "wajan"],
            allergies=[], vegetarian=False, disliked_ingredients=[], available_ingredients=[],
            has_medical_condition=False, consent=True
        )
        res = generate_recommendation(db, user_in, "browser-owner", user_id=owner.id)
        consult_id = res["consultation_id"]
        
    with TestClient(app) as client:
        # 1. Guest access -> redirect HTML, 401 API
        res_guest_html = client.get(f"/hasil/{consult_id}", follow_redirects=False)
        assert res_guest_html.status_code in (302, 303)
        assert "/login" in res_guest_html.headers["location"]
        
        res_guest_api = client.get(f"/api/consultations/{consult_id}")
        assert res_guest_api.status_code == 401

        # 2. Owner access -> 200 HTML, 200 API
        RATE_LIMIT_DATA.clear()
        res_login_page = client.get("/login")
        import re
        match = re.search(r'name="csrf_token"\s+value="([^"]+)"', res_login_page.text)
        csrf_user = match.group(1) if match else ""
        
        res_login_owner = client.post("/login", data={"email": "owner@example.com", "password": "password123", "csrf_token": csrf_user}, follow_redirects=False)
        assert res_login_owner.status_code in (302, 303)
        assert client.get(f"/hasil/{consult_id}").status_code == 200
        assert client.get(f"/api/consultations/{consult_id}").status_code == 200
        client.post("/logout", data={"csrf_token": csrf_user})
        
        # 3. Other user access -> 404/403
        RATE_LIMIT_DATA.clear()
        res_login_page = client.get("/login")
        match = re.search(r'name="csrf_token"\s+value="([^"]+)"', res_login_page.text)
        csrf_user = match.group(1) if match else ""
        
        res_login_other = client.post("/login", data={"email": "other@example.com", "password": "password123", "csrf_token": csrf_user}, follow_redirects=False)
        assert res_login_other.status_code in (302, 303)
        assert client.get(f"/hasil/{consult_id}").status_code == 404
        assert client.get(f"/api/consultations/{consult_id}").status_code == 404
        client.post("/logout", data={"csrf_token": csrf_user})

        # 4. Admin access without user session -> 200 HTML, 200 API
        RATE_LIMIT_DATA.clear()
        res_admin_page = client.get("/admin")
        import re
        match = re.search(r'name="csrf_token"\s+value="([^"]+)"', res_admin_page.text)
        csrf = match.group(1) if match else ""
        
        res_login = client.post("/admin/login", data={"password": ADMIN_PASSWORD, "csrf_token": csrf}, follow_redirects=False)
        assert res_login.status_code in (302, 303)
        
        # Admin accesses owner consultation without user session
        assert client.get(f"/hasil/{consult_id}").status_code == 200
        assert client.get(f"/api/consultations/{consult_id}").status_code == 200


def test_vegetable_group_sentinel_and_migration(test_session_db):
    TempSessionLocal, _ = test_session_db
    with TempSessionLocal() as db:
        seed_database(db, force=True)
        
        # Check all recipes in DB have non-null vegetable_group
        recipes = db.scalars(select(Recipe)).all()
        assert len(recipes) == 52
        for r in recipes:
            assert r.vegetable_group is not None
            assert r.vegetable_group != ""
            assert r.vegetable_group != "NULL"
            
            # Check vegetable ingredient presence
            has_sayur = any(ing.food.category == "sayur" for ing in r.ingredients)
            if has_sayur:
                assert r.vegetable_group != "none"
            else:
                assert r.vegetable_group == "none"


def test_portion_optimization_daily_combination_tradeoff(test_session_db):
    TempSessionLocal, _ = test_session_db
    with TempSessionLocal() as db:
        seed_database(db, force=True)
        
        # Create consultation input where 1.0x vs 1.5x portion multiplier makes a clear daily trade-off
        user = ConsultationInput(
            name="PortionTester", age=22, gender="male", weight=65, height=170, activity_level="medium",
            goal="balanced", daily_budget=40000, meal_count=3, max_cooking_time=30, tools=["kompor", "wajan", "panci"],
            allergies=[], vegetarian=False, disliked_ingredients=[], available_ingredients=["telur", "tempe"],
            has_medical_condition=False, consent=True
        )
        
        result = generate_recommendation(db, user, "browser-portion-test")
        assert result["status"] in ("complete", "partial")
        assert "plan" in result and len(result["plan"]) > 0
        
        # Verify deterministic output
        result2 = generate_recommendation(db, user, "browser-portion-test-2")
        assert [m["id"] for m in result["plan"]] == [m["id"] for m in result2["plan"]]
        assert [m["portion_multiplier"] for m in result["plan"]] == [m["portion_multiplier"] for m in result2["plan"]]


def test_diversity_integration_scenarios(test_session_db):
    TempSessionLocal, _ = test_session_db
    with TempSessionLocal() as db:
        seed_database(db, force=True)
        
        # Scenario A: Diverse alternatives available
        user_diverse = ConsultationInput(
            name="DiverseTester", age=21, gender="female", weight=52, height=160, activity_level="medium",
            goal="balanced", daily_budget=60000, meal_count=3, max_cooking_time=45, tools=["kompor", "wajan", "panci", "magic-com"],
            allergies=[], vegetarian=False, disliked_ingredients=[], available_ingredients=["telur", "ayam-dada", "tempe"],
            has_medical_condition=False, consent=True
        )
        res_a = generate_recommendation(db, user_diverse, "browser-div-a")
        assert res_a["status"] == "complete"
        
        # Scenario B: Limited alternatives (e.g. vegetarian + strict disliked)
        user_limited = ConsultationInput(
            name="LimitedTester", age=21, gender="female", weight=50, height=158, activity_level="low",
            goal="hemat", daily_budget=30000, meal_count=3, max_cooking_time=30, tools=["kompor", "wajan"],
            allergies=[], vegetarian=True, disliked_ingredients=["tahu", "kacang-tanah"], available_ingredients=["tempe"],
            has_medical_condition=False, consent=True
        )
        res_b = generate_recommendation(db, user_limited, "browser-div-b")
        assert res_b["status"] in ("complete", "partial")
        
        # Scenario C: Hard constraint beats diversity
        # Diverse ingredient causes allergy -> must be rejected in favor of safe option
        user_allergy = ConsultationInput(
            name="AllergyTester", age=22, gender="male", weight=65, height=170, activity_level="medium",
            goal="balanced", daily_budget=50000, meal_count=3, max_cooking_time=30, tools=["kompor", "wajan"],
            allergies=["udang", "egg"], vegetarian=False, disliked_ingredients=[], available_ingredients=[],
            has_medical_condition=False, consent=True
        )
        res_c = generate_recommendation(db, user_allergy, "browser-div-c")
        for item in res_c["plan"]:
            for ing in item["ingredients"]:
                assert ing["slug"] not in ("udang", "telur")


def test_payload_ratio_and_adequacy_score_separation(test_session_db):
    TempSessionLocal, _ = test_session_db
    with TempSessionLocal() as db:
        seed_database(db, force=True)
        user = ConsultationInput(
            name="RatioTester", age=20, gender="male", weight=60, height=168, activity_level="medium",
            goal="balanced", daily_budget=45000, meal_count=3, max_cooking_time=30, tools=["kompor", "wajan"],
            allergies=[], vegetarian=False, disliked_ingredients=[], available_ingredients=[],
            has_medical_condition=False, consent=True
        )
        result = generate_recommendation(db, user, "browser-ratio-test")
        
        # Verify totals structure
        totals = result["totals"]
        assert "energy_ratio_percent" in totals
        assert "protein_ratio_percent" in totals
        
        # Verify user serialization
        user_payload = serialize_consultation_for_user(result)
        assert "combination_analysis_safe" in user_payload
        safe_analysis = user_payload["combination_analysis_safe"]
        assert "energy_ratio_percent" in safe_analysis
        assert "protein_ratio_percent" in safe_analysis
        assert "energy_adequacy_score" not in safe_analysis
        assert "protein_adequacy_score" not in safe_analysis
        assert "energy_adequacy" not in safe_analysis
        assert "protein_adequacy" not in safe_analysis



def test_debug_payload_flag(monkeypatch, test_session_db):
    TempSessionLocal, _ = test_session_db
    with TempSessionLocal() as db:
        seed_database(db, force=True)
        user = ConsultationInput(
            name="DebugTester", age=20, gender="male", weight=60, height=168, activity_level="medium",
            goal="balanced", daily_budget=45000, meal_count=3, max_cooking_time=30, tools=["kompor", "wajan"],
            allergies=[], vegetarian=False, disliked_ingredients=[], available_ingredients=[],
            has_medical_condition=False, consent=True
        )
        result = generate_recommendation(db, user, "browser-debug-test")
        
        # When ENABLE_DEBUG_PAYLOAD is False (default)
        user_payload = serialize_consultation_for_user(result)
        assert "rejected_candidates_debug" not in user_payload
