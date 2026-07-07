from __future__ import annotations

from app.expert_system import estimate_energy_target, left_shoulder, nutrition_balance_score, right_shoulder, triangle
from app.schemas import ConsultationInput


def test_membership_functions_are_bounded():
    for value in [-1, 0, 0.5, 1, 2]:
        assert 0 <= left_shoulder(value, 0.5, 1.0) <= 1
        assert 0 <= right_shoulder(value, 0.5, 1.0) <= 1
        assert 0 <= triangle(value, 0, 0.5, 1) <= 1


def test_nutrition_score_is_bounded():
    score = nutrition_balance_score({"energy": 600, "protein": 22, "fat": 18, "carbs": 80, "fiber": 6, "cost": 10000}, "lunch")
    assert 0 <= score <= 100


def test_weight_goals_change_energy_target():
    base = dict(name="T", age=21, gender="male", weight=60, height=165, activity_level="medium", daily_budget=40000,
                meal_count=3, max_cooking_time=30, tools=["kompor"], consent=True)
    lose = estimate_energy_target(ConsultationInput(**base, goal="lose"))["target"]
    maintain = estimate_energy_target(ConsultationInput(**base, goal="maintain"))["target"]
    gain = estimate_energy_target(ConsultationInput(**base, goal="gain"))["target"]
    assert lose < maintain < gain
