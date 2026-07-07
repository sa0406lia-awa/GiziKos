from __future__ import annotations

import os
import tempfile
from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.database import Base
from app.seed import seed_database
from app.models import User, Consultation
from app.schemas import ConsultationInput
from app.services import (
    generate_recommendation,
    serialize_consultation_for_user,
    serialize_consultation_for_admin,
    normalize_legacy_consultation_result,
)
from app.main import app, RATE_LIMIT_DATA
from app.auth import create_user
from app.config import ADMIN_PASSWORD


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


def test_debug_payload_security_matrix(monkeypatch, temp_db_env):
    SessionLocal, _ = temp_db_env
    with SessionLocal() as db:
        seed_database(db, force=True)
        u = create_user(db, "RegularUser", "user@example.com", "password123")
        
        inp = ConsultationInput(
            name="RegularUser", age=21, gender="male", weight=60, height=168, activity_level="medium",
            goal="balanced", daily_budget=45000, meal_count=3, max_cooking_time=30, tools=["kompor", "wajan"],
            allergies=[], vegetarian=False, disliked_ingredients=[], available_ingredients=[],
            has_medical_condition=False, consent=True
        )
        res = generate_recommendation(db, inp, "b-mat", user_id=u.id)
        cid = res["consultation_id"]
        
    with TestClient(app) as client:
        # Login user
        RATE_LIMIT_DATA.clear()
        res_lp = client.get("/login")
        import re
        match = re.search(r'name="csrf_token"\s+value="([^"]+)"', res_lp.text)
        csrf = match.group(1) if match else ""
        client.post("/login", data={"email": "user@example.com", "password": "password123", "csrf_token": csrf})
        
        # User + flag false -> safe payload
        monkeypatch.setattr("app.main.ENABLE_DEBUG_PAYLOAD", False)
        r_user_false = client.get(f"/api/consultations/{cid}")
        assert r_user_false.status_code == 200
        data_uf = r_user_false.json()
        assert "rejected_candidates_debug" not in data_uf
        assert "combination_analysis_safe" in data_uf
        assert "energy_adequacy_score" not in data_uf["combination_analysis_safe"]
        
        # User + flag true -> STILL safe payload! (Flag does not override user auth)
        monkeypatch.setattr("app.main.ENABLE_DEBUG_PAYLOAD", True)
        r_user_true = client.get(f"/api/consultations/{cid}")
        assert r_user_true.status_code == 200
        data_ut = r_user_true.json()
        assert "rejected_candidates_debug" not in data_ut
        assert "combination_analysis_safe" in data_ut
        assert "energy_adequacy_score" not in data_ut["combination_analysis_safe"]
        
        client.post("/logout", data={"csrf_token": csrf})
        
        # Admin login
        RATE_LIMIT_DATA.clear()
        res_ap = client.get("/admin")
        match = re.search(r'name="csrf_token"\s+value="([^"]+)"', res_ap.text)
        csrf_adm = match.group(1) if match else ""
        client.post("/admin/login", data={"password": ADMIN_PASSWORD, "csrf_token": csrf_adm})
        
        # Admin + flag false -> admin audit safe (no rejected debug)
        monkeypatch.setattr("app.main.ENABLE_DEBUG_PAYLOAD", False)
        r_adm_false = client.get(f"/api/consultations/{cid}")
        assert r_adm_false.status_code == 200
        data_af = r_adm_false.json()
        assert "rejected_candidates_debug" not in data_af
        
        # Admin + flag true -> full debug!
        monkeypatch.setattr("app.main.ENABLE_DEBUG_PAYLOAD", True)
        r_adm_true = client.get(f"/api/consultations/{cid}")
        assert r_adm_true.status_code == 200
        data_at = r_adm_true.json()
        assert "combination_analysis" in data_at


def test_legacy_consultation_normalizer():
    legacy_payload = {
        "consultation_id": "leg-1",
        "totals": {
            "energy": 1800.0,
            "energy_target": 2000.0,
            "protein": 75.0,
            "protein_target": 60.0,
            "cost": 35000.0,
            "energy_adequacy": 90.0,
        },
        "profile": {"goal": "Memperbaiki pola makan seimbang"}
    }
    normalized = normalize_legacy_consultation_result(legacy_payload)
    assert normalized["totals"]["energy_ratio_percent"] == 90.0
    assert normalized["totals"]["protein_ratio_percent"] == 125.0


def test_neutral_protein_evaluation_label(temp_db_env):
    SessionLocal, _ = temp_db_env
    with SessionLocal() as db:
        seed_database(db, force=True)
        inp = ConsultationInput(
            name="ProtTester", age=21, gender="male", weight=60, height=168, activity_level="medium",
            goal="balanced", daily_budget=50000, meal_count=3, max_cooking_time=30, tools=["kompor", "wajan"],
            allergies=[], vegetarian=False, disliked_ingredients=[], available_ingredients=[],
            has_medical_condition=False, consent=True
        )
        res = generate_recommendation(db, inp, "b-prot")
        assert "protein_evaluation_label" in res["totals"]
        label = res["totals"]["protein_evaluation_label"]
        assert label in ["di bawah target model", "sesuai rentang model", "di atas target model"]
        # Check no medical claims in warnings or messages
        for w in res["warnings"]:
            assert "medis" not in w.lower() or "ruang lingkup" in w.lower() or "dokter" in w.lower()


def test_balanced_vs_maintain_goal_scores(temp_db_env):
    SessionLocal, _ = temp_db_env
    with SessionLocal() as db:
        seed_database(db, force=True)
        
        inp_bal = ConsultationInput(
            name="GoalTester", age=21, gender="male", weight=65, height=170, activity_level="medium",
            goal="balanced", daily_budget=50000, meal_count=3, max_cooking_time=30, tools=["kompor", "wajan"],
            allergies=[], vegetarian=False, disliked_ingredients=[], available_ingredients=[],
            has_medical_condition=False, consent=True
        )
        res_bal = generate_recommendation(db, inp_bal, "b-bal")
        
        inp_maint = ConsultationInput(
            name="GoalTester", age=21, gender="male", weight=65, height=170, activity_level="medium",
            goal="maintain", daily_budget=50000, meal_count=3, max_cooking_time=30, tools=["kompor", "wajan"],
            allergies=[], vegetarian=False, disliked_ingredients=[], available_ingredients=[],
            has_medical_condition=False, consent=True
        )
        res_maint = generate_recommendation(db, inp_maint, "b-maint")
        
        score_bal = res_bal["combination_analysis"]["goal_component_scores"]
        score_maint = res_maint["combination_analysis"]["goal_component_scores"]
        
        # Goal component scores for balanced must differ from maintain
        assert score_bal != score_maint
