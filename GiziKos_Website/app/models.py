from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(600))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    consultations: Mapped[list["Consultation"]] = relationship(back_populates="user")


class Food(Base):
    __tablename__ = "foods"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    category: Mapped[str] = mapped_column(String(60), index=True)
    energy: Mapped[float] = mapped_column(Float, default=0)
    protein: Mapped[float] = mapped_column(Float, default=0)
    fat: Mapped[float] = mapped_column(Float, default=0)
    carbs: Mapped[float] = mapped_column(Float, default=0)
    fiber: Mapped[float] = mapped_column(Float, default=0)
    price_per_100g: Mapped[float] = mapped_column(Float, default=0)
    allergens: Mapped[str] = mapped_column(String(200), default="")
    tags: Mapped[str] = mapped_column(String(300), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # Metadata dataset
    source_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    verification_status: Mapped[str | None] = mapped_column(String(50), nullable=True, default="demo")
    data_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_location: Mapped[str | None] = mapped_column(String(100), nullable=True)
    price_date: Mapped[str | None] = mapped_column(String(50), nullable=True)
    food_state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    portion_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)

    ingredients: Mapped[list["RecipeIngredient"]] = relationship(back_populates="food")


class Recipe(Base):
    __tablename__ = "recipes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    meal_type: Mapped[str] = mapped_column(String(30), index=True)
    cooking_time: Mapped[int] = mapped_column(Integer)
    tools: Mapped[str] = mapped_column(String(300), default="")
    tags: Mapped[str] = mapped_column(String(300), default="")
    steps_json: Mapped[str] = mapped_column(Text, default="[]")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # Metadata dataset & Klasifikasi variasi
    source_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    verification_status: Mapped[str | None] = mapped_column(String(50), nullable=True, default="demo")
    data_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_location: Mapped[str | None] = mapped_column(String(100), nullable=True)
    price_date: Mapped[str | None] = mapped_column(String(50), nullable=True)
    food_state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    portion_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    main_protein: Mapped[str | None] = mapped_column(String(100), nullable=True)
    main_carbohydrate: Mapped[str | None] = mapped_column(String(100), nullable=True)
    vegetable_group: Mapped[str | None] = mapped_column(String(200), nullable=True)

    ingredients: Mapped[list["RecipeIngredient"]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan", lazy="selectin"
    )


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredients"
    __table_args__ = (UniqueConstraint("recipe_id", "food_id", name="uq_recipe_food"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id", ondelete="CASCADE"))
    food_id: Mapped[int] = mapped_column(ForeignKey("foods.id", ondelete="RESTRICT"))
    grams: Mapped[float] = mapped_column(Float)

    recipe: Mapped[Recipe] = relationship(back_populates="ingredients")
    food: Mapped[Food] = relationship(back_populates="ingredients", lazy="joined")


class Consultation(Base):
    __tablename__ = "consultations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    browser_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="Mahasiswa")
    input_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    user: Mapped[User | None] = relationship(back_populates="consultations")


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(String(500), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
