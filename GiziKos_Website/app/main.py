from __future__ import annotations

import json
import secrets
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from .auth import authenticate_user, create_user
from .config import ADMIN_PASSWORD, APP_NAME, APP_VERSION, BASE_DIR, SECRET_KEY, GIZIKOS_ENV, ENABLE_DEBUG_PAYLOAD
from .database import Base, SessionLocal, engine, get_db, upgrade_schema, initialize_or_migrate_database
from .models import Consultation, Food, Recipe, SystemSetting, User
from .schemas import ConsultationInput
from .seed import seed_database
from .services import generate_recommendation, get_consultation, get_history, serialize_consultation_for_user, serialize_consultation_for_admin, normalize_legacy_consultation_result


# In-memory rate limiting data
RATE_LIMIT_DATA = defaultdict(list)

def get_rate_limit_key(request: Request) -> str:
    ip = request.client.host if request.client else "unknown"
    session_id = request.session.get("user_id", "guest")
    return f"{ip}:{session_id}"

def check_rate_limit(key: str, max_requests: int, window_seconds: int) -> bool:
    now = time.time()
    timestamps = [t for t in RATE_LIMIT_DATA[key] if now - t < window_seconds]
    RATE_LIMIT_DATA[key] = timestamps
    if len(timestamps) >= max_requests:
        return False
    RATE_LIMIT_DATA[key].append(now)
    return True

def rate_limiter(max_requests: int, window_seconds: int):
    def dependency(request: Request):
        key = f"{request.url.path}:{get_rate_limit_key(request)}"
        if not check_rate_limit(key, max_requests, window_seconds):
            raise HTTPException(
                status_code=429,
                detail="Terlalu banyak permintaan. Silakan coba kembali beberapa saat lagi."
            )
    return dependency


# CSRF Verification
async def verify_csrf(request: Request):
    if request.method == "POST":
        form = await request.form()
        token_in_form = form.get("csrf_token")
        token_in_session = request.session.get("csrf_token")
        if not token_in_session or token_in_form != token_in_session:
            raise HTTPException(
                status_code=403,
                detail="Token CSRF tidak valid atau telah kedaluwarsa. Permintaan ditolak."
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_or_migrate_database()
    yield



app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    same_site="lax",
    https_only=(GIZIKOS_ENV == "production"),
    max_age=60 * 60 * 24 * 14,
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")

def format_currency(value):
    try:
        return "{:,.0f}".format(value or 0).replace(',', '.')
    except (ValueError, TypeError):
        return value

templates.env.filters["format_currency"] = format_currency


def get_browser_id(request: Request) -> str:
    browser_id = request.cookies.get("gizikos_browser")
    try:
        uuid.UUID(browser_id or "")
        return browser_id or str(uuid.uuid4())
    except ValueError:
        return str(uuid.uuid4())


def current_user(request: Request, db: Session) -> User | None:
    user_id = request.session.get("user_id")
    return db.get(User, user_id) if user_id else None


def flash(request: Request, message: str, kind: str = "success") -> None:
    request.session["flash"] = {"message": message, "kind": kind}


def safe_next(value: str | None) -> str:
    if not value:
        return "/akun"
    parsed = urlparse(value)
    return value if not parsed.netloc and value.startswith("/") else "/akun"


def get_csrf_token(request: Request) -> str:
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_hex(32)
    return request.session["csrf_token"]


def render(request: Request, template: str, context: dict | None = None, status_code: int = 200, db: Session | None = None) -> HTMLResponse:
    user = current_user(request, db) if db else None
    token = get_csrf_token(request)
    payload = {
        "request": request,
        "app_name": APP_NAME,
        "version": APP_VERSION,
        "current_user": user,
        "flash": request.session.pop("flash", None),
        "csrf_token": token,
    }
    payload.update(context or {})
    response = templates.TemplateResponse(request=request, name=template, context=payload, status_code=status_code)
    if not request.cookies.get("gizikos_browser"):
        response.set_cookie("gizikos_browser", get_browser_id(request), max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax")
    return response


def food_categories(db: Session) -> dict[str, list[Food]]:
    foods = db.scalars(select(Food).where(Food.active.is_(True)).order_by(Food.category, Food.name)).all()
    categories: dict[str, list[Food]] = {}
    for food in foods:
        categories.setdefault(food.category, []).append(food)
    return categories


@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    stats = {
        "foods": db.scalar(select(func.count(Food.id)).where(Food.active.is_(True))) or 0,
        "recipes": db.scalar(select(func.count(Recipe.id)).where(Recipe.active.is_(True))) or 0,
        "consultations": db.scalar(select(func.count(Consultation.id))) or 0,
        "users": db.scalar(select(func.count(User.id))) or 0,
    }
    return render(request, "home.html", {"stats": stats}, db=db)


@app.get("/daftar", response_class=HTMLResponse)
def register_page(request: Request, db: Session = Depends(get_db)):
    if current_user(request, db):
        return RedirectResponse("/akun", status_code=303)
    return render(request, "register.html", db=db)


@app.post("/daftar", dependencies=[Depends(verify_csrf), Depends(rate_limiter(5, 60))])
async def register_submit(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    name = str(form.get("name") or "")
    email = str(form.get("email") or "")
    password = str(form.get("password") or "")
    confirmation = str(form.get("password_confirmation") or "")
    if password != confirmation:
        return render(request, "register.html", {"error": "Konfirmasi kata sandi tidak sama.", "form_data": {"name": name, "email": email}}, 422, db=db)
    try:
        user = create_user(db, name, email, password)
    except ValueError as exc:
        return render(request, "register.html", {"error": str(exc), "form_data": {"name": name, "email": email}}, 422, db=db)
    # Session regeneration
    csrf_val = request.session.get("csrf_token")
    request.session.clear()
    if csrf_val:
        request.session["csrf_token"] = csrf_val
    flash(request, "Akun berhasil dibuat. Silakan masuk untuk melanjutkan.")
    return RedirectResponse("/login?next=/", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str | None = None, db: Session = Depends(get_db)):
    if current_user(request, db):
        return RedirectResponse("/akun", status_code=303)
    return render(request, "login.html", {"next_url": safe_next(next)}, db=db)


@app.post("/login", dependencies=[Depends(verify_csrf), Depends(rate_limiter(5, 60))])
async def login_submit(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    email = str(form.get("email") or "")
    password = str(form.get("password") or "")
    next_url = safe_next(str(form.get("next") or "/akun"))
    user = authenticate_user(db, email, password)
    if not user:
        return render(request, "login.html", {"error": "Email atau kata sandi tidak sesuai.", "form_data": {"email": email}, "next_url": next_url}, 401, db=db)
    # Session regeneration
    csrf_val = request.session.get("csrf_token")
    request.session.clear()
    if csrf_val:
        request.session["csrf_token"] = csrf_val
    request.session["user_id"] = user.id
    flash(request, f"Selamat datang kembali, {user.name}!")
    redirect_target = "/" if next_url == "/akun" else next_url
    return RedirectResponse(redirect_target, status_code=303)


@app.post("/logout", dependencies=[Depends(verify_csrf)])
def logout(request: Request):
    request.session.clear()
    request.session["flash"] = {"message": "Anda berhasil keluar dari akun.", "kind": "info"}
    return RedirectResponse("/", status_code=303)


@app.get("/akun", response_class=HTMLResponse)
def account_page(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login?next=/akun", status_code=303)
    history = get_history(db, get_browser_id(request), user.id, limit=6)
    return render(request, "account.html", {"history": history}, db=db)


@app.get("/cek-gizi", response_class=HTMLResponse)
def consultation_form(request: Request, db: Session = Depends(get_db)):
    if not current_user(request, db):
        flash(request, "Silakan masuk ke akun Anda terlebih dahulu untuk menggunakan fitur Cek Gizi.", "warning")
        return RedirectResponse("/login?next=/cek-gizi", status_code=303)
    return render(request, "consultation.html", {"categories": food_categories(db)}, db=db)


@app.post("/cek-gizi", dependencies=[Depends(verify_csrf)])
async def consultation_submit(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        flash(request, "Silakan masuk ke akun Anda terlebih dahulu untuk menggunakan fitur Cek Gizi.", "warning")
        return RedirectResponse("/login?next=/cek-gizi", status_code=303)
    form = await request.form()
    selected_tools = list(form.getlist("tools"))
    if "none" in selected_tools:
        selected_tools = []
    try:
        payload = ConsultationInput(
            name=str(form.get("name") or user.name),
            age=int(form.get("age") or 0),
            gender=str(form.get("gender") or ""),
            weight=float(form.get("weight") or 0),
            height=float(form.get("height") or 0),
            activity_level=str(form.get("activity_level") or ""),
            goal=str(form.get("goal") or ""),
            daily_budget=int(form.get("daily_budget") or 0),
            meal_count=int(form.get("meal_count") or 3),
            max_cooking_time=int(form.get("max_cooking_time") or 0),
            tools=selected_tools,
            allergies=list(form.getlist("allergies")),
            vegetarian=form.get("vegetarian") == "on",
            disliked_ingredients=list(form.getlist("disliked_ingredients")),
            available_ingredients=list(form.getlist("available_ingredients")),
            has_medical_condition=form.get("has_medical_condition") == "on",
            consent=form.get("consent") == "on",
        )
    except Exception as exc:
        return render(request, "consultation.html", {"categories": food_categories(db), "error": f"Data belum valid: {exc}", "form_data": dict(form)}, 422, db=db)

    if not payload.consent:
        return render(request, "consultation.html", {"categories": food_categories(db), "error": "Persetujuan disclaimer wajib dicentang."}, 422, db=db)

    result = generate_recommendation(db, payload, get_browser_id(request), user.id)
    response = RedirectResponse(url=f"/hasil/{result['consultation_id']}", status_code=status.HTTP_303_SEE_OTHER)
    if not request.cookies.get("gizikos_browser"):
        response.set_cookie("gizikos_browser", get_browser_id(request), max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax")
    return response


@app.get("/hasil/{consultation_id}", response_class=HTMLResponse)
def result_page(consultation_id: str, request: Request, db: Session = Depends(get_db)):
    is_admin = bool(request.session.get("admin"))
    user = current_user(request, db)

    if not user and not is_admin:
        flash(request, "Silakan masuk ke akun Anda terlebih dahulu untuk melihat hasil.", "warning")
        return RedirectResponse(f"/login?next=/hasil/{consultation_id}", status_code=303)
        
    consultation = db.get(Consultation, consultation_id)
    if not consultation:
        return render(request, "404.html", {"message": "Hasil konsultasi tidak ditemukan."}, 404, db=db)
        
    if not is_admin:
        if consultation.user_id != user.id:
            return render(request, "404.html", {"message": "Hasil konsultasi tidak ditemukan."}, 404, db=db)

    result = normalize_legacy_consultation_result(json.loads(consultation.result_json))

    include_debug = is_admin and ENABLE_DEBUG_PAYLOAD
    if include_debug:
        result = serialize_consultation_for_admin(result)
    else:
        result = serialize_consultation_for_user(result)
        
    return render(request, "result.html", {"result": result}, db=db)


@app.get("/riwayat", response_class=HTMLResponse)
def history_page(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    history = get_history(db, get_browser_id(request), user.id if user else None)
    return render(request, "history.html", {"history": history, "account_history": bool(user)}, db=db)


@app.get("/metode", response_class=HTMLResponse)
def method_page(request: Request, db: Session = Depends(get_db)):
    return render(request, "method.html", db=db)


@app.get("/referensi", response_class=HTMLResponse)
def references_page(request: Request, db: Session = Depends(get_db)):
    return render(request, "references.html", db=db)


@app.get("/tentang", response_class=HTMLResponse)
def about_page(request: Request, db: Session = Depends(get_db)):
    return render(request, "about.html", db=db)


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("admin"):
        return render(request, "admin_login.html", db=db)
    settings = db.scalars(select(SystemSetting).order_by(SystemSetting.key)).all()
    foods = db.scalars(select(Food).order_by(Food.category, Food.name).limit(150)).all()
    recipes = db.scalars(select(Recipe).order_by(Recipe.meal_type, Recipe.name)).all()
    counts = {
        "foods": db.scalar(select(func.count(Food.id))) or 0,
        "recipes": db.scalar(select(func.count(Recipe.id))) or 0,
        "consultations": db.scalar(select(func.count(Consultation.id))) or 0,
        "users": db.scalar(select(func.count(User.id))) or 0,
    }
    return render(request, "admin.html", {"settings": settings, "foods": foods, "recipes": recipes, "counts": counts}, db=db)


@app.post("/admin/login", dependencies=[Depends(verify_csrf), Depends(rate_limiter(5, 60))])
async def admin_login(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    password = str(form.get("password") or "")
    if not secrets.compare_digest(password, ADMIN_PASSWORD):
        return render(request, "admin_login.html", {"error": "Kata sandi admin tidak sesuai."}, 401, db=db)
    request.session["admin"] = True
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/logout", dependencies=[Depends(verify_csrf)])
def admin_logout(request: Request):
    request.session.pop("admin", None)
    return RedirectResponse("/", status_code=303)


@app.post("/admin/settings", dependencies=[Depends(verify_csrf)])
async def update_settings(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("admin"):
        raise HTTPException(status_code=403, detail="Akses admin diperlukan.")
    form = await request.form()
    for setting in db.scalars(select(SystemSetting)).all():
        if setting.key in form:
            setting.value = str(form[setting.key]).strip()
    db.commit()
    return RedirectResponse("/admin#settings", status_code=303)


@app.post("/admin/foods/{food_id}/price", dependencies=[Depends(verify_csrf)])
async def update_food_price(food_id: int, request: Request, db: Session = Depends(get_db)):
    if not request.session.get("admin"):
        raise HTTPException(status_code=403, detail="Akses admin diperlukan.")
    form = await request.form()
    food = db.get(Food, food_id)
    if not food:
        raise HTTPException(status_code=404, detail="Bahan tidak ditemukan.")
    try:
        price = float(form.get("price_per_100g") or 0)
        if price < 0:
            raise ValueError
    except ValueError:
        raise HTTPException(status_code=422, detail="Harga tidak valid.")
    food.price_per_100g = price
    db.commit()
    return RedirectResponse("/admin#foods", status_code=303)


@app.post("/admin/recipes/{recipe_id}/toggle", dependencies=[Depends(verify_csrf)])
def toggle_recipe(recipe_id: int, request: Request, db: Session = Depends(get_db)):
    if not request.session.get("admin"):
        raise HTTPException(status_code=403, detail="Akses admin diperlukan.")
    recipe = db.get(Recipe, recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Resep tidak ditemukan.")
    recipe.active = not recipe.active
    db.commit()
    return RedirectResponse("/admin#recipes", status_code=303)


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    return {
        "status": "ok",
        "app": APP_NAME,
        "version": APP_VERSION,
        "database": "ok",
        "foods": db.scalar(select(func.count(Food.id)).where(Food.active.is_(True))) or 0,
        "recipes": db.scalar(select(func.count(Recipe.id)).where(Recipe.active.is_(True))) or 0,
        "users": db.scalar(select(func.count(User.id))) or 0,
        "consultations": db.scalar(select(func.count(Consultation.id))) or 0,
    }


@app.get("/api/foods")
def api_foods(db: Session = Depends(get_db)):
    foods = db.scalars(select(Food).where(Food.active.is_(True)).order_by(Food.category, Food.name)).all()
    return [{
        "id": item.id,
        "slug": item.slug,
        "name": item.name,
        "category": item.category,
        "energy": item.energy,
        "protein": item.protein,
        "fat": item.fat,
        "carbs": item.carbs,
        "fiber": item.fiber,
        "price_per_100g": item.price_per_100g,
        "allergens": [x for x in item.allergens.split(";") if x],
        "tags": [x for x in item.tags.split(";") if x],
    } for item in foods]


@app.post("/api/recommend", dependencies=[Depends(rate_limiter(10, 60))])
def api_recommend(payload: ConsultationInput, request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Akses ditolak. Silakan masuk terlebih dahulu.")
    result = generate_recommendation(db, payload, get_browser_id(request), user.id)
    is_admin = bool(request.session.get("admin"))
    include_debug = is_admin and ENABLE_DEBUG_PAYLOAD
    if include_debug:
        serialized = serialize_consultation_for_admin(result)
    else:
        serialized = serialize_consultation_for_user(result)
    response = JSONResponse(serialized)
    if not request.cookies.get("gizikos_browser"):
        response.set_cookie("gizikos_browser", get_browser_id(request), max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax")
    return response


@app.get("/api/consultations/{consultation_id}")
def api_consultation(consultation_id: str, request: Request, db: Session = Depends(get_db)):
    is_admin = bool(request.session.get("admin"))
    user = current_user(request, db)
    if not user and not is_admin:
        raise HTTPException(status_code=401, detail="Akses ditolak. Silakan masuk terlebih dahulu.")
        
    consultation = db.get(Consultation, consultation_id)
    if not consultation:
        raise HTTPException(status_code=404, detail="Konsultasi tidak ditemukan.")
        
    if not is_admin:
        if consultation.user_id != user.id:
            raise HTTPException(status_code=404, detail="Konsultasi tidak ditemukan.")
        
    result = normalize_legacy_consultation_result(json.loads(consultation.result_json))

    include_debug = is_admin and ENABLE_DEBUG_PAYLOAD
    if include_debug:
        return serialize_consultation_for_admin(result)
    else:
        return serialize_consultation_for_user(result)


@app.exception_handler(400)
def bad_request_error(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"status": "error", "message": str(exc.detail if hasattr(exc, "detail") else exc)}, status_code=400)
    with SessionLocal() as db:
        return render(request, "error.html", {"message": "Permintaan tidak valid."}, 400, db=db)


@app.exception_handler(401)
def unauthorized_error(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"status": "error", "message": str(exc.detail if hasattr(exc, "detail") else exc)}, status_code=401)
    with SessionLocal() as db:
        flash(request, "Silakan masuk ke akun Anda terlebih dahulu.", "warning")
        return RedirectResponse("/login", status_code=303)


@app.exception_handler(403)
def forbidden_error(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"status": "error", "message": str(exc.detail if hasattr(exc, "detail") else exc)}, status_code=403)
    with SessionLocal() as db:
        return render(request, "error.html", {"message": str(exc.detail if hasattr(exc, "detail") else exc)}, 403, db=db)


@app.exception_handler(404)
def not_found(request: Request, exc):
    with SessionLocal() as db:
        return render(request, "404.html", {"message": "Halaman yang Anda cari tidak ditemukan."}, 404, db=db)


@app.exception_handler(422)
def validation_error(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"status": "error", "message": "Input tidak valid.", "details": str(exc)}, status_code=422)
    with SessionLocal() as db:
        return render(request, "error.html", {"message": "Data input tidak valid."}, 422, db=db)


@app.exception_handler(429)
def rate_limit_error(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"status": "error", "message": str(exc.detail if hasattr(exc, "detail") else exc)}, status_code=429)
    with SessionLocal() as db:
        return render(request, "error.html", {"message": str(exc.detail if hasattr(exc, "detail") else exc)}, 429, db=db)


@app.exception_handler(500)
def server_error(request: Request, exc: Exception):
    import traceback
    traceback.print_exc()
    with SessionLocal() as db:
        if GIZIKOS_ENV == "development":
            return render(
                request,
                "error.html",
                {"message": f"Terjadi kesalahan internal: {exc} ({type(exc).__name__})"},
                500,
                db=db,
            )
        return render(
            request,
            "error.html",
            {"message": "Terjadi kesalahan internal. Silakan coba kembali."},
            500,
            db=db,
        )

