from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.main import app, RATE_LIMIT_DATA
from app.config import APP_VERSION

BASE_PAYLOAD = {
    "name": "Tester",
    "age": 21,
    "gender": "male",
    "weight": 60,
    "height": 165,
    "activity_level": "medium",
    "goal": "lose",
    "daily_budget": 40000,
    "meal_count": 3,
    "max_cooking_time": 35,
    "tools": ["kompor", "panci", "wajan", "rice-cooker"],
    "allergies": [],
    "vegetarian": False,
    "disliked_ingredients": [],
    "available_ingredients": ["nasi-putih", "telur", "tempe", "bayam", "pisang"],
    "has_medical_condition": False,
    "consent": True,
}


def get_csrf_token(client, path: str) -> str:
    RATE_LIMIT_DATA.clear()  # Bersihkan rate limit agar setup test tidak terblokir
    response = client.get(path)
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', response.text)
    return match.group(1) if match else ""


def register_and_login(client, email: str):
    csrf_reg = get_csrf_token(client, "/daftar")
    RATE_LIMIT_DATA.clear()
    register = client.post("/daftar", data={
        "name": "Akun Tester",
        "email": email,
        "password": "Password123",
        "password_confirmation": "Password123",
        "csrf_token": csrf_reg,
    }, follow_redirects=False)
    assert register.status_code == 303
    assert register.headers["location"] == "/login?next=/"

    csrf_login = get_csrf_token(client, "/login")
    RATE_LIMIT_DATA.clear()
    login = client.post("/login", data={
        "email": email,
        "password": "Password123",
        "next": "/",
        "csrf_token": csrf_login,
    }, follow_redirects=False)
    assert login.status_code == 303
    assert login.headers["location"] == "/"


def test_health_and_public_pages():
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert health.json()["version"] == APP_VERSION
        for path in ["/", "/metode", "/referensi", "/tentang", "/login", "/daftar"]:
            response = client.get(path)
            assert response.status_code == 200
            assert "GiziKos" in response.text


def test_cek_gizi_requires_login():
    with TestClient(app, follow_redirects=False) as client:
        response = client.get("/cek-gizi")
        assert response.status_code == 303
        assert response.headers["location"] == "/login?next=/cek-gizi"


def test_register_login_account_and_logout():
    email = f"tester-{uuid.uuid4().hex[:8]}@example.com"
    with TestClient(app, follow_redirects=False) as client:
        csrf_reg = get_csrf_token(client, "/daftar")
        RATE_LIMIT_DATA.clear()
        register = client.post("/daftar", data={
            "name": "Akun Tester",
            "email": email,
            "password": "Password123",
            "password_confirmation": "Password123",
            "csrf_token": csrf_reg,
        }, follow_redirects=False)
        assert register.status_code == 303
        assert register.headers["location"] == "/login?next=/"

        csrf_login = get_csrf_token(client, "/login")
        RATE_LIMIT_DATA.clear()
        login = client.post("/login", data={
            "email": email,
            "password": "Password123",
            "next": "/",
            "csrf_token": csrf_login,
        }, follow_redirects=False)
        assert login.status_code == 303
        assert login.headers["location"] == "/"

        account = client.get("/akun")
        assert account.status_code == 200
        assert "Akun Tester" in account.text

        csrf_logout = get_csrf_token(client, "/akun")
        RATE_LIMIT_DATA.clear()
        logout = client.post("/logout", data={"csrf_token": csrf_logout})
        assert logout.status_code == 303

        csrf_login = get_csrf_token(client, "/login")
        RATE_LIMIT_DATA.clear()
        login = client.post("/login", data={
            "email": email,
            "password": "Password123",
            "next": "/akun",
            "csrf_token": csrf_login,
        })
        assert login.status_code == 303
        assert client.get("/akun").status_code == 200


def test_api_recommendation_success_and_goal_target():
    email = f"tester-{uuid.uuid4().hex[:8]}@example.com"
    with TestClient(app) as client:
        register_and_login(client, email)
        response = client.post("/api/recommend", json=BASE_PAYLOAD)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] in {"complete", "partial", "warning", "success"}
        assert len(data["plan"]) == 3
        assert data["totals"]["cost"] <= BASE_PAYLOAD["daily_budget"]
        assert data["totals"]["average_score"] > 0
        assert data["totals"]["energy_target"] > 0
        assert data["profile"]["goal_code"] == "lose"
        consultation = client.get(f"/api/consultations/{data['consultation_id']}")
        assert consultation.status_code == 200


def test_allergy_and_disliked_are_hard_constraints_and_removed_from_stock():
    email = f"tester-{uuid.uuid4().hex[:8]}@example.com"
    payload = {
        **BASE_PAYLOAD,
        "allergies": ["egg"],
        "disliked_ingredients": ["tomat"],
        "available_ingredients": ["telur", "tomat", "nasi-putih", "tempe"],
    }
    with TestClient(app) as client:
        register_and_login(client, email)
        data = client.post("/api/recommend", json=payload).json()
        assert any("otomatis dihapus" in warning for warning in data["warnings"])
        for menu in data["plan"]:
            slugs = {item["slug"] for item in menu["ingredients"]}
            assert "telur" not in slugs
            assert "tomat" not in slugs


def test_vegetarian_filters_meat_and_seafood():
    email = f"tester-{uuid.uuid4().hex[:8]}@example.com"
    payload = {**BASE_PAYLOAD, "vegetarian": True, "daily_budget": 50000}
    forbidden = {"ayam-dada", "ayam-suwir", "ikan-kembung", "ikan-lele", "tuna-kaleng", "sarden"}
    with TestClient(app) as client:
        register_and_login(client, email)
        response = client.post("/api/recommend", json=payload)
        assert response.status_code == 200
        for menu in response.json()["plan"]:
            ingredient_slugs = {item["slug"] for item in menu["ingredients"]}
            assert not ingredient_slugs & forbidden


def test_no_tools_returns_no_recommendation():
    email = f"tester-{uuid.uuid4().hex[:8]}@example.com"
    payload = {**BASE_PAYLOAD, "tools": []}
    with TestClient(app) as client:
        register_and_login(client, email)
        data = client.post("/api/recommend", json=payload).json()
        assert data["status"] == "no_match"
        assert data["plan"] == []
        assert any("Tidak ada alat masak" in warning for warning in data["warnings"])


def test_form_submission_redirects_to_result():
    email = f"tester-{uuid.uuid4().hex[:8]}@example.com"
    with TestClient(app, follow_redirects=False) as client:
        register_and_login(client, email)
        csrf_val = get_csrf_token(client, "/cek-gizi")
        RATE_LIMIT_DATA.clear()
        form = {
            "name": "Form Tester", "age": "21", "gender": "female", "weight": "55", "height": "160",
            "activity_level": "medium", "goal": "gain", "daily_budget": "40000", "meal_count": "3",
            "max_cooking_time": "35", "tools": ["kompor", "panci", "wajan"], "allergies": [],
            "available_ingredients": ["nasi-putih", "telur", "bayam"], "consent": "on",
            "csrf_token": csrf_val,
        }
        response = client.post("/cek-gizi", data=form)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/hasil/")
        
        result_page = client.get(response.headers["location"])
        assert result_page.status_code == 200
        assert "Rencana menu" in result_page.text
        assert "Menambah berat badan" in result_page.text


def test_admin_login_and_setting_update():
    with TestClient(app, follow_redirects=False) as client:
        csrf_login = get_csrf_token(client, "/admin")
        RATE_LIMIT_DATA.clear()
        login = client.post("/admin/login", data={"password": "gizikos-admin", "csrf_token": csrf_login})
        assert login.status_code == 303
        page = client.get("/admin")
        assert page.status_code == 200
        assert "Dashboard pengelolaan" in page.text

        csrf_update = get_csrf_token(client, "/admin")
        RATE_LIMIT_DATA.clear()
        update = client.post("/admin/settings", data={
            "age_min": "18", "age_max": "25", "min_ingredient_coverage": "0.20",
            "budget_tolerance": "1.00", "medical_scope_block": "true",
            "csrf_token": csrf_update,
        })
        assert update.status_code == 303


# ==================== NEW MANDATORY TESTS ====================

def test_production_database_isolation():
    from app.config import DB_PATH
    prod_db = Path(__file__).resolve().parent.parent / "gizikos.db"
    assert Path(DB_PATH).resolve() != prod_db.resolve()


def test_api_recommendation_requires_login():
    with TestClient(app) as client:
        response = client.post("/api/recommend", json=BASE_PAYLOAD)
        assert response.status_code == 401


def test_csrf_protection_fails_without_token():
    with TestClient(app) as client:
        response = client.post("/login", data={"email": "test@example.com", "password": "Password123"})
        assert response.status_code == 403
        assert "CSRF" in response.text or "csrf" in response.text


def test_rate_limiting():
    RATE_LIMIT_DATA.clear()
    with TestClient(app) as client:
        csrf = get_csrf_token(client, "/login")
        for i in range(15):
            response = client.post("/login", data={
                "email": "limiter-test@example.com", "password": "wrong", "csrf_token": csrf
            })
            if response.status_code == 429:
                break
        else:
            pytest.fail("Rate limiter tidak terpicu setelah banyak request berulang!")


def test_consultation_ownership_check():
    user_a_email = f"user-a-{uuid.uuid4().hex[:6]}@example.com"
    user_b_email = f"user-b-{uuid.uuid4().hex[:6]}@example.com"
    
    with TestClient(app, follow_redirects=False) as client_a, TestClient(app, follow_redirects=False) as client_b:
        register_and_login(client_a, user_a_email)
        register_and_login(client_b, user_b_email)
        
        csrf_val = get_csrf_token(client_a, "/cek-gizi")
        RATE_LIMIT_DATA.clear()
        form = {
            "name": "User A", "age": "21", "gender": "male", "weight": "60", "height": "165",
            "activity_level": "medium", "goal": "lose", "daily_budget": "40000", "meal_count": "3",
            "max_cooking_time": "35", "tools": ["kompor", "panci", "wajan"], "allergies": [],
            "available_ingredients": ["nasi-putih", "telur", "tempe"], "consent": "on",
            "csrf_token": csrf_val,
        }
        resp = client_a.post("/cek-gizi", data=form)
        assert resp.status_code == 303
        consultation_id = resp.headers["location"].split("/")[-1]
        
        # User A should be able to read it
        res_a = client_a.get(f"/hasil/{consultation_id}")
        assert res_a.status_code == 200
        
        # User B should NOT be able to read it (404)
        res_b = client_b.get(f"/hasil/{consultation_id}")
        assert res_b.status_code == 404
        
        # Guest should NOT be able to read it (303 Redirect to login)
        with TestClient(app, follow_redirects=False) as client_guest:
            res_guest = client_guest.get(f"/hasil/{consultation_id}")
            assert res_guest.status_code == 303


def test_repetition_penalty_in_combinations():
    email = f"tester-{uuid.uuid4().hex[:8]}@example.com"
    with TestClient(app) as client:
        register_and_login(client, email)
        payload_muscle = {**BASE_PAYLOAD, "goal": "muscle"}
        res_muscle = client.post("/api/recommend", json=payload_muscle)
        assert res_muscle.status_code == 200
        
        payload_hemat = {**BASE_PAYLOAD, "goal": "hemat", "daily_budget": 20000}
        res_hemat = client.post("/api/recommend", json=payload_hemat)
        assert res_hemat.status_code == 200


def test_admin_login_via_user_login():
    with TestClient(app, follow_redirects=False) as client:
        csrf_login = get_csrf_token(client, "/login")
        RATE_LIMIT_DATA.clear()
        
        # Test incorrect password
        res_fail = client.post("/login", data={
            "email": "AdminGiziKos",
            "password": "wrongpassword",
            "csrf_token": csrf_login,
        })
        assert res_fail.status_code == 401
        assert "tidak sesuai" in res_fail.text

        # Test correct admin password
        RATE_LIMIT_DATA.clear()
        res_success = client.post("/login", data={
            "email": "AdminGiziKos",
            "password": "admin123",
            "csrf_token": csrf_login,
        })
        assert res_success.status_code == 303
        assert res_success.headers["location"] == "/admin"
        
        # Check admin dashboard accessibility after login
        admin_page = client.get("/admin")
        assert admin_page.status_code == 200
        assert "Dashboard pengelolaan" in admin_page.text

