from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import User

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_ITERATIONS = 310_000


def normalize_email(email: str) -> str:
    return email.strip().lower()


def validate_email(email: str) -> bool:
    return bool(EMAIL_RE.fullmatch(normalize_email(email)))


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("Kata sandi minimal 8 karakter.")
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        _ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_b64, digest_b64 = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_b64.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def create_user(db: Session, name: str, email: str, password: str) -> User:
    email = normalize_email(email)
    if len(name.strip()) < 2:
        raise ValueError("Nama minimal 2 karakter.")
    if not validate_email(email):
        raise ValueError("Format email tidak valid.")
    if db.scalar(select(User.id).where(User.email == email)):
        raise ValueError("Email sudah terdaftar.")
    user = User(id=str(uuid.uuid4()), name=name.strip()[:120], email=email, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.email == normalize_email(email), User.active.is_(True)))
    if user and verify_password(password, user.password_hash):
        return user
    return None
