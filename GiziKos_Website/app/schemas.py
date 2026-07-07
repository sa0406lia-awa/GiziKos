from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ConsultationInput(BaseModel):
    name: str = Field(default="Mahasiswa", min_length=1, max_length=80)
    age: int = Field(ge=18, le=60)
    gender: Literal["male", "female"]
    weight: float = Field(gt=30, lt=200)
    height: float = Field(gt=130, lt=220)
    activity_level: Literal["low", "medium", "high"]
    goal: Literal["balanced", "maintain", "lose", "gain", "muscle", "hemat"]
    daily_budget: int = Field(ge=15000, le=150000)
    meal_count: Literal[2, 3] = 3
    max_cooking_time: int = Field(ge=5, le=120)
    tools: list[str] = Field(default_factory=list)
    allergies: list[str] = Field(default_factory=list)
    vegetarian: bool = False
    disliked_ingredients: list[str] = Field(default_factory=list)
    available_ingredients: list[str] = Field(default_factory=list)
    has_medical_condition: bool = False
    consent: bool = True

    @field_validator("tools", "allergies", "disliked_ingredients", "available_ingredients", mode="before")
    @classmethod
    def normalize_list(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return [str(part).strip() for part in value if str(part).strip()]

    @model_validator(mode="after")
    def normalize_unique_values(self):
        self.tools = sorted(set(self.tools) - {"none"})
        self.allergies = sorted(set(self.allergies))
        self.disliked_ingredients = sorted(set(self.disliked_ingredients))
        self.available_ingredients = sorted(set(self.available_ingredients) - set(self.disliked_ingredients))
        return self


class RecommendationResponse(BaseModel):
    consultation_id: str
    status: str
    message: str
    plan: list[dict]
    totals: dict
    shopping_list: list[dict]
    fired_rules: list[dict]
    warnings: list[str]
