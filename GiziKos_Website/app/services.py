import itertools
import json
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .config import COMBINATION_WEIGHTS
from .expert_system import estimate_energy_target, fuzzy_score, run_forward_chaining, serialize_recipe, split_values, evaluate_daily_combinations
from .models import Consultation, Food, Recipe, RecipeIngredient, SystemSetting
from .schemas import ConsultationInput

MEAL_LABELS = {"breakfast": "Sarapan", "lunch": "Makan Siang", "dinner": "Makan Malam"}
GOAL_LABELS = {
    "balanced": "Memperbaiki pola makan seimbang",
    "maintain": "Mempertahankan berat badan",
    "lose": "Menurunkan berat badan secara bertahap",
    "gain": "Menambah berat badan secara bertahap",
    "muscle": "Mendukung peningkatan massa otot",
    "hemat": "Mendapatkan menu paling hemat",
}


def get_settings(db: Session) -> dict[str, str]:
    return {item.key: item.value for item in db.scalars(select(SystemSetting)).all()}


def get_recipes(db: Session) -> list[Recipe]:
    stmt = (
        select(Recipe)
        .where(Recipe.active.is_(True))
        .options(selectinload(Recipe.ingredients).joinedload(RecipeIngredient.food))
        .order_by(Recipe.meal_type, Recipe.name)
    )
    return list(db.scalars(stmt).unique().all())


def sanitize_available_ingredients(db: Session, user: ConsultationInput) -> tuple[list[str], list[str]]:
    selected = set(user.available_ingredients)
    if not selected:
        return [], []
    foods = db.scalars(select(Food).where(Food.slug.in_(selected))).all()
    disliked = set(user.disliked_ingredients)
    allergies = set(user.allergies)
    safe: list[str] = []
    removed: list[str] = []
    for food in foods:
        blocked = food.slug in disliked or bool(split_values(food.allergens) & allergies)
        if user.vegetarian and "vegetarian" not in split_values(food.tags):
            blocked = True
        if blocked:
            removed.append(food.name)
        else:
            safe.append(food.slug)
    return sorted(safe), sorted(removed)


RULE_DETAILS_MAP = {
    "R060": {
        "conclusion": "coverage_high",
        "contribution": "Menghemat biaya belanja karena sebagian besar bahan (>=75%) sudah tersedia."
    },
    "R061": {
        "conclusion": "coverage_medium",
        "contribution": "Memanfaatkan sebagian stok bahan yang ada."
    },
    "R062": {
        "conclusion": "coverage_low",
        "contribution": "Resep lolos tetapi memerlukan pembelian bahan tambahan."
    },
    "R070": {
        "conclusion": "protein_ok",
        "contribution": "Membantu memenuhi asupan protein sekali makan dengan kandungan protein memadai."
    },
    "R071": {
        "conclusion": "fiber_ok",
        "contribution": "Mendukung pencernaan dengan kandungan serat yang baik."
    },
    "R080": {
        "conclusion": "eligible",
        "contribution": "Memenuhi semua batasan kelayakan (alat, alergi, preferensi, waktu, dan anggaran)."
    }
}

REJECTION_DETAILS_MAP = {
    "R001": "Usia di luar target utama mahasiswa kos (18-25 tahun).",
    "R002": "Kondisi medis khusus berada di luar ruang lingkup GiziKos.",
    "R003": "Tidak ada alat masak yang dipilih oleh pengguna.",
    "R010": "Mengandung alergen yang harus dihindari pengguna.",
    "R020": "Mengandung daging/ikan (tidak cocok untuk vegetarian).",
    "R021": "Mengandung bahan makanan yang tidak disukai pengguna.",
    "R030": "Peralatan memasak yang dibutuhkan tidak tersedia di kos.",
    "R040": "Waktu memasak melebihi batas waktu maksimal pengguna.",
    "R050": "Biaya menu melebihi anggaran makan per porsi."
}

def build_detailed_supporting_rules(selected_plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    detailed_rules = []
    seen_codes = set()
    for menu in selected_plan:
        for rule in menu.get("fired_rules", []):
            code = rule["code"]
            if code in seen_codes:
                continue
            seen_codes.add(code)
            
            fact_map = {
                "R060": f"Cakupan bahan tersedia {menu['fuzzy']['coverage']:.0f}% >= 75%",
                "R061": f"Cakupan bahan tersedia {menu['fuzzy']['coverage']:.0f}% berada di rentang 20-75%",
                "R062": f"Cakupan bahan tersedia {menu['fuzzy']['coverage']:.0f}% < 20%",
                "R070": f"Protein menu {menu['nutrition']['protein']:.1f}g >= 15g",
                "R071": f"Serat menu {menu['nutrition']['fiber']:.1f}g >= 4g",
                "R080": "Tidak ada pelanggaran batasan wajib",
            }
            trigger_fact = fact_map.get(code, "Kriteria terpenuhi")
            details = RULE_DETAILS_MAP.get(code, {
                "conclusion": ", ".join(rule.get("conclusions", [])),
                "contribution": "Memenuhi aturan sistem."
            })
            
            detailed_rules.append({
                "code": code,
                "description": rule["description"],
                "trigger_fact": trigger_fact,
                "conclusion": details["conclusion"],
                "contribution": details["contribution"]
            })
    return sorted(detailed_rules, key=lambda r: r["code"])

def generate_recommendation(db: Session, user: ConsultationInput, browser_id: str, user_id: str | None = None) -> dict[str, Any]:
    settings = get_settings(db)
    safe_available, removed_available = sanitize_available_ingredients(db, user)
    user.available_ingredients = safe_available
    recipes = get_recipes(db)
    meal_types = ["breakfast", "lunch", "dinner"] if user.meal_count == 3 else ["lunch", "dinner"]
    
    candidates_by_meal: dict[str, list[Any]] = defaultdict(list)
    rejected_summary: dict[str, int] = defaultdict(int)
    rejection_summary = {
        "scope": 0,
        "medical": 0,
        "tools": 0,
        "allergy": 0,
        "vegetarian": 0,
        "disliked": 0,
        "time": 0,
        "budget": 0
    }
    rejected_candidates_debug = []
    rejection_rules_counts = defaultdict(int)
    all_fired_rules: list[dict[str, Any]] = []

    # Tahap 1: Evaluasi kandidat per waktu makan dengan portion scaling
    portions = [0.75, 1.0, 1.25, 1.5]
    for recipe in recipes:
        if recipe.meal_type not in meal_types:
            continue
        
        any_eligible = False
        recipe_rejections = []
        recipe_fired_rejection_rules = []
        
        for port in portions:
            result = run_forward_chaining(recipe, user, settings, portion_multiplier=port)
            all_fired_rules.extend(result.fired_rules)
            if result.eligible:
                fuzzy = fuzzy_score(result, user, settings)
                result.score = fuzzy["score"]
                result.fuzzy_breakdown = fuzzy
                result.portion_multiplier = port
                candidates_by_meal[recipe.meal_type].append(result)
                any_eligible = True
            else:
                recipe_rejections.append(result.rejection_reasons)
                recipe_fired_rejection_rules.append(result.fired_rules)
                
        if not any_eligible:
            # Jika semua porsi ditolak, log alasan penolakan porsi standar 1.0
            default_idx = 1
            reasons = recipe_rejections[default_idx] if len(recipe_rejections) > default_idx else (recipe_rejections[0] if recipe_rejections else [])
            fired_rules_rej = recipe_fired_rejection_rules[default_idx] if len(recipe_fired_rejection_rules) > default_idx else (recipe_fired_rejection_rules[0] if recipe_fired_rejection_rules else [])
            for reason in reasons:
                rejected_summary[reason] += 1
            for rule in fired_rules_rej:
                code = rule["code"]
                if any(concl.startswith("reject_") for concl in rule["conclusions"]):
                    rejection_rules_counts[code] += 1
                
                # Tambah ke kategori rejection_summary
                if code == "R001":
                    rejection_summary["scope"] += 1
                elif code == "R002":
                    rejection_summary["medical"] += 1
                elif code in ("R003", "R030"):
                    rejection_summary["tools"] += 1
                elif code == "R010":
                    rejection_summary["allergy"] += 1
                elif code == "R020":
                    rejection_summary["vegetarian"] += 1
                elif code == "R021":
                    rejection_summary["disliked"] += 1
                elif code == "R040":
                    rejection_summary["time"] += 1
                elif code == "R050":
                    rejection_summary["budget"] += 1

            rejected_candidates_debug.append({
                "recipe_name": recipe.name,
                "recipe_slug": recipe.slug,
                "meal_type": recipe.meal_type,
                "fired_rejection_rules": [r["code"] for r in fired_rules_rej if any(c.startswith("reject_") for c in r.get("conclusions", []))]
            })

    # Ambil 16 kandidat terbaik per jenis makan dengan tie-breaker deterministik
    for mt in meal_types:
        candidates_by_meal[mt].sort(key=lambda r: (-r.score, r.recipe.slug, r.portion_multiplier))
        candidates_by_meal[mt] = candidates_by_meal[mt][:16]

    # Tahap 2: Evaluasi kombinasi harian
    best_combo = None
    best_combo_details = None
    has_alternative_no_rep = False

    has_all_meals = all(len(candidates_by_meal[mt]) > 0 for mt in meal_types)

    if has_all_meals:
        list_of_lists = [candidates_by_meal[mt] for mt in meal_types]
        for combo in itertools.product(*list_of_lists):
            details = evaluate_daily_combinations(list(combo), user, settings, COMBINATION_WEIGHTS)
            if details.get("total_repetition_penalty", 0.0) == 0.0:
                has_alternative_no_rep = True
            
            if is_better_combination(details, best_combo_details):
                best_combo = list(combo)
                best_combo_details = details

    selected: list[dict[str, Any]] = []
    alternatives: dict[str, list[dict[str, Any]]] = {}
    available = set(user.available_ingredients)

    if best_combo:
        for res in best_combo:
            item = serialize_recipe(res, res.fuzzy_breakdown, available)
            item["meal_label"] = MEAL_LABELS[res.recipe.meal_type]
            selected.append(item)
            
            # Ambil alternatif menu sejenis (resep unik yang berbeda dari terpilih)
            seen_slugs = {res.recipe.slug}
            alternatives[res.recipe.meal_type] = []
            for alt_res in candidates_by_meal[res.recipe.meal_type]:
                if alt_res.recipe.slug not in seen_slugs:
                    seen_slugs.add(alt_res.recipe.slug)
                    serialized = serialize_recipe(alt_res, alt_res.fuzzy_breakdown, available)
                    serialized["meal_label"] = MEAL_LABELS[res.recipe.meal_type]
                    alternatives[res.recipe.meal_type].append(serialized)
                    if len(alternatives[res.recipe.meal_type]) == 3:
                        break
    else:
        # Fallback jika hanya sebagian jenis makanan yang ditemukan menunya
        for meal_type in meal_types:
            ranked = candidates_by_meal.get(meal_type, [])
            if not ranked:
                continue
            chosen_res = ranked[0]
            item = serialize_recipe(chosen_res, chosen_res.fuzzy_breakdown, available)
            item["meal_label"] = MEAL_LABELS[meal_type]
            selected.append(item)
            
            seen_slugs = {chosen_res.recipe.slug}
            alternatives[meal_type] = []
            for alt_res in ranked:
                if alt_res.recipe.slug not in seen_slugs:
                    seen_slugs.add(alt_res.recipe.slug)
                    serialized = serialize_recipe(alt_res, alt_res.fuzzy_breakdown, available)
                    serialized["meal_label"] = MEAL_LABELS[meal_type]
                    alternatives[meal_type].append(serialized)
                    if len(alternatives[meal_type]) == 3:
                        break

    # Hitung total nutrisi dan biaya
    energy_target = estimate_energy_target(user)
    totals = calculate_totals(selected, user.daily_budget, energy_target["target"], user=user)
    if best_combo_details:
        totals["average_score"] = best_combo_details["score"]

    variation_warning = False
    variation_message = None
    if best_combo_details:
        total_rep = best_combo_details.get("total_repetition_penalty", 0.0)
        has_repetition = total_rep > 0.0
        if has_repetition:
            variation_warning = True
            if has_alternative_no_rep:
                variation_message = "Pengulangan menu terjadi untuk mengoptimalkan kecukupan gizi atau anggaran, meskipun terdapat alternatif lain."
            else:
                variation_message = "Variasi menu terbatas karena anggaran, alat, alergi, atau pilihan resep yang tersedia."
        
        combination_analysis = {
            "energy_ratio_percent": totals["energy_ratio_percent"],
            "energy_adequacy_score": best_combo_details.get("energy_adequacy", 0.0),
            "protein_ratio_percent": totals["protein_ratio_percent"],
            "protein_adequacy_score": best_combo_details.get("protein_adequacy", 0.0),
            "energy_adequacy": best_combo_details.get("energy_adequacy", 0.0),
            "protein_adequacy": best_combo_details.get("protein_adequacy", 0.0),
            "budget_adequacy": best_combo_details.get("budget_score", 0.0),
            "diversity_score": best_combo_details.get("diversity_score", 0.0),
            "goal_component_scores": best_combo_details.get("goal_component_scores", {}),
            "protein_repetition_penalty": best_combo_details.get("protein_repetition_penalty", 0.0),
            "carbohydrate_repetition_penalty": best_combo_details.get("carbohydrate_repetition_penalty", 0.0),
            "vegetable_repetition_penalty": best_combo_details.get("vegetable_repetition_penalty", 0.0),
            "method_repetition_penalty": best_combo_details.get("method_repetition_penalty", 0.0),
            "total_repetition_penalty": total_rep,
            "unique_protein_count": best_combo_details.get("unique_protein_count", 0),
            "unique_vegetable_count": best_combo_details.get("unique_vegetable_count", 0),
            "variation_warning": variation_warning,
            "variation_message": variation_message,
        }
    else:
        combination_analysis = {
            "energy_ratio_percent": totals["energy_ratio_percent"],
            "energy_adequacy_score": 0.0,
            "protein_ratio_percent": totals["protein_ratio_percent"],
            "protein_adequacy_score": 0.0,
            "energy_adequacy": 0.0,
            "protein_adequacy": 0.0,
            "budget_adequacy": 0.0,
            "diversity_score": 0.0,
            "goal_component_scores": {},
            "protein_repetition_penalty": 0.0,
            "carbohydrate_repetition_penalty": 0.0,
            "vegetable_repetition_penalty": 0.0,
            "method_repetition_penalty": 0.0,
            "total_repetition_penalty": 0.0,
            "unique_protein_count": 0,
            "unique_vegetable_count": 0,
            "variation_warning": False,
            "variation_message": None,
        }

    warnings: list[str] = []
    if removed_available:
        warnings.append("Bahan stok berikut otomatis dihapus karena bertentangan dengan alergi, preferensi, atau pola vegetarian: " + ", ".join(removed_available) + ".")
    if not user.tools:
        warnings.append("Tidak ada alat masak yang dipilih, sehingga sistem tidak memberikan rekomendasi menu.")
    if user.has_medical_condition:
        warnings.append("Kondisi medis khusus berada di luar ruang lingkup GiziKos. Konsultasikan kebutuhan makan dengan tenaga kesehatan.")
    if user.age < int(settings.get("age_min", "18")) or user.age > int(settings.get("age_max", "25")):
        warnings.append("Profil usia berada di luar target utama mahasiswa 18–25 tahun.")
    if variation_warning and variation_message:
        warnings.append(variation_message)
    if totals.get("protein_ratio_percent", 0) > 150.0:
        warnings.append("Protein menu berada cukup jauh di atas target model GiziKos. Pertimbangkan menyesuaikan porsi atau memilih alternatif menu yang lebih seimbang.")


    # Evaluasi kecukupan untuk menentukan status secara ketat
    has_all_meals = (len(selected) == len(meal_types))
    cost_ok = (totals["cost"] <= user.daily_budget)
    energy_ratio = totals["energy"] / max(energy_target["target"], 1.0)
    energy_ok = (0.80 <= energy_ratio <= 1.20)
    
    # 0.8 untuk non-muscle, 1.2 untuk muscle
    min_protein_required = user.weight * (1.2 if user.goal == "muscle" else 0.8)
    protein_ok = (totals["protein"] >= min_protein_required)

    if not selected:
        status = "no_match"
        message = "Belum ditemukan kombinasi menu yang memenuhi batasan utama pengguna. Coba sesuaikan anggaran, waktu memasak, alat, atau preferensi."
        warnings.append("Batasan Anda terlalu ketat. Sesuaikan anggaran, alat, atau alergi.")
    elif has_all_meals and cost_ok and energy_ok and protein_ok:
        status = "complete"
        message = "Rekomendasi lengkap memenuhi kriteria energi, protein minimum, anggaran, dan batasan pengguna dalam model prototipe GiziKos."
    else:
        status = "partial"
        message = "Rekomendasi aman berhasil disusun, tetapi pemenuhan energi atau protein minimum belum mencapai kriteria model prototipe GiziKos."
        if not has_all_meals:
            missing = [MEAL_LABELS[m] for m in meal_types if not any(item["meal_type"] == m for item in selected)]
            warnings.append("Tidak ada kandidat yang lolos untuk: " + ", ".join(missing) + ". Sesuaikan alat, waktu, atau anggaran.")
        elif not cost_ok:
            warnings.append("Estimasi biaya menu melebihi anggaran harian Anda.")
        elif not energy_ok:
            warnings.append(f"Total energi menu ({totals['energy']:.0f} kkal) berada di luar target ideal 80-120% ({totals['energy_target']*0.8:.0f}-{totals['energy_target']*1.2:.0f} kkal).")
        elif not protein_ok:
            warnings.append(f"Total protein menu ({totals['protein']:.1f}g) berada di bawah batas minimum ({min_protein_required:.1f}g) untuk menjaga keseimbangan gizi.")

    # Aturan aktif pendukung menu terpilih
    selected_rules = []
    seen_selected_rules = set()
    for menu in selected:
        for rule in menu.get("fired_rules", []):
            code = rule["code"]
            # Hanya aturan pendukung yang dicantumkan
            if not any(concl.startswith("reject_") for concl in rule.get("conclusions", [])):
                rule_key = (code, menu["name"])
                if rule_key in seen_selected_rules:
                    continue
                seen_selected_rules.add(rule_key)
                
                fact_map = {
                    "R060": f"Cakupan bahan tersedia {menu['fuzzy']['coverage']:.0f}% >= 75%",
                    "R061": f"Cakupan bahan tersedia {menu['fuzzy']['coverage']:.0f}% berada di rentang 20-75%",
                    "R062": f"Cakupan bahan tersedia {menu['fuzzy']['coverage']:.0f}% < 20%",
                    "R070": f"Protein menu {menu['nutrition']['protein']:.1f}g >= 15g",
                    "R071": f"Serat menu {menu['nutrition']['fiber']:.1f}g >= 4g",
                    "R080": "Tidak ada pelanggaran batasan wajib",
                }
                trigger_fact = fact_map.get(code, "Kriteria terpenuhi")
                details = RULE_DETAILS_MAP.get(code, {
                    "conclusion": ", ".join(rule.get("conclusions", [])),
                    "contribution": "Memenuhi aturan sistem."
                })
                selected_rules.append({
                    "code": code,
                    "description": rule["description"],
                    "trigger_fact": trigger_fact,
                    "conclusion": details["conclusion"],
                    "contribution": details["contribution"],
                    "menu_related": menu["name"]
                })
    selected_rules = sorted(selected_rules, key=lambda r: (r["code"], r["menu_related"]))

    # Aturan yang menolak kandidat
    rejection_rules = []
    for code, count in rejection_rules_counts.items():
        rejection_rules.append({
            "code": code,
            "description": REJECTION_DETAILS_MAP.get(code, "Batasan wajib dilanggar"),
            "count": count
        })
    rejection_rules = sorted(rejection_rules, key=lambda r: r["code"])

    shopping_list = build_shopping_list(selected, available)
    profile = build_profile_summary(user, energy_target)
    consultation_id = str(uuid.uuid4())

    result_payload = {
        "consultation_id": consultation_id,
        "status": status,
        "message": message,
        "profile": profile,
        "plan": selected,
        "alternatives": alternatives,
        "totals": totals,
        "shopping_list": shopping_list,
        "selected_rules": selected_rules,
        "rejection_rules": rejection_rules,
        "rejection_summary": rejection_summary,
        "rejected_candidates_debug": rejected_candidates_debug,
        "warnings": warnings,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "disclaimer": "GiziKos merupakan sistem rekomendasi prototipe dan bukan pengganti konsultasi dokter atau ahli gizi.",
        "combination_analysis": combination_analysis,
    }

    db.add(
        Consultation(
            id=consultation_id,
            browser_id=browser_id,
            user_id=user_id,
            name=user.name,
            input_json=user.model_dump_json(),
            result_json=json.dumps(result_payload, ensure_ascii=False),
        )
    )
    db.commit()
    return result_payload


def calculate_totals(plan: list[dict[str, Any]], daily_budget: int, energy_target: float, user: ConsultationInput | None = None) -> dict[str, Any]:
    keys = ["energy", "protein", "fat", "carbs", "fiber", "cost"]
    totals = {key: round(sum(item["nutrition"][key] for item in plan), 2) for key in keys}
    totals["daily_budget"] = daily_budget
    totals["remaining_budget"] = max(0, round(daily_budget - totals["cost"], 2))
    totals["budget_usage_percent"] = round((totals["cost"] / daily_budget * 100), 1) if daily_budget else 0
    totals["average_score"] = round(sum(item["score"] for item in plan) / max(len(plan), 1), 1)
    totals["energy_target"] = round(energy_target)
    totals["energy_ratio_percent"] = round((totals["energy"] / energy_target * 100), 1) if energy_target else 0
    totals["energy_target_percent"] = totals["energy_ratio_percent"]
    totals["energy_gap"] = round(energy_target - totals["energy"])

    protein_factor = 1.6 if (user and user.goal == "muscle") else 1.0
    weight = user.weight if user else 60.0
    protein_target = weight * protein_factor
    totals["protein_target"] = round(protein_target, 1)
    totals["protein_ratio_percent"] = round((totals["protein"] / protein_target * 100), 1) if protein_target else 0
    
    prot_ratio = totals["protein_ratio_percent"]
    if prot_ratio < 80.0:
        totals["protein_evaluation_label"] = "di bawah target model"
    elif prot_ratio <= 150.0:
        totals["protein_evaluation_label"] = "sesuai rentang model"
    else:
        totals["protein_evaluation_label"] = "di atas target model"
        
    return totals



def build_shopping_list(plan: list[dict[str, Any]], available: set[str]) -> list[dict[str, Any]]:
    aggregate: dict[str, dict[str, Any]] = {}
    for menu in plan:
        for ingredient in menu["ingredients"]:
            if ingredient["slug"] in available:
                continue
            row = aggregate.setdefault(ingredient["slug"], {"slug": ingredient["slug"], "name": ingredient["name"], "grams": 0.0, "category": ingredient["category"]})
            row["grams"] += ingredient["grams"]
    return sorted([{**item, "grams": round(item["grams"], 1)} for item in aggregate.values()], key=lambda item: (item["category"], item["name"]))


def deduplicate_rules(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for item in rules:
        code = item["code"]
        if code not in output:
            output[code] = {**item, "count": 1}
        else:
            output[code]["count"] += 1
    return sorted(output.values(), key=lambda item: (-item["priority"], item["code"]))


def build_profile_summary(user: ConsultationInput, energy_target: dict[str, float]) -> dict[str, Any]:
    bmi = user.weight / ((user.height / 100) ** 2)
    activity_labels = {"low": "Ringan", "medium": "Sedang", "high": "Tinggi"}
    bmi_label = "Berat badan rendah" if bmi < 18.5 else "Rentang umum" if bmi < 25 else "Di atas rentang umum"
    return {
        "name": user.name,
        "age": user.age,
        "gender": "Laki-laki" if user.gender == "male" else "Perempuan",
        "weight": user.weight,
        "height": user.height,
        "bmi": round(bmi, 1),
        "bmi_label": bmi_label,
        "activity": activity_labels[user.activity_level],
        "goal": GOAL_LABELS[user.goal],
        "goal_code": user.goal,
        "daily_budget": user.daily_budget,
        "meal_count": user.meal_count,
        "max_cooking_time": user.max_cooking_time,
        "vegetarian": user.vegetarian,
        "allergies": user.allergies,
        "energy_target": energy_target,
    }


def get_consultation(db: Session, consultation_id: str) -> dict[str, Any] | None:
    consultation = db.get(Consultation, consultation_id)
    return normalize_legacy_consultation_result(json.loads(consultation.result_json)) if consultation else None



def get_history(db: Session, browser_id: str, user_id: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
    stmt = select(Consultation)
    if user_id:
        stmt = stmt.where(Consultation.user_id == user_id)
    else:
        stmt = stmt.where(Consultation.browser_id == browser_id)
    stmt = stmt.order_by(Consultation.created_at.desc()).limit(limit)
    output = []
    for item in db.scalars(stmt).all():
        result = json.loads(item.result_json)
        output.append({
            "id": item.id,
            "name": item.name,
            "created_at": item.created_at,
            "status": result.get("status"),
            "total_cost": result.get("totals", {}).get("cost", 0),
            "average_score": result.get("totals", {}).get("average_score", 0),
            "goal": result.get("profile", {}).get("goal", ""),
        })
    return output


def is_better_combination(new_details: dict[str, Any], best_details: dict[str, Any] | None) -> bool:
    if best_details is None:
        return True
    
    score_diff = new_details["score"] - best_details["score"]
    
    # If the score difference is greater than 2.0, the one with the higher score wins outright.
    if score_diff > 2.0:
        return True
    elif score_diff < -2.0:
        return False
        
    # If the score difference is within [-2.0, 2.0], we use the tie-breaker rules
    # 1. repetition penalty lebih rendah
    rep_diff = new_details["total_repetition_penalty"] - best_details["total_repetition_penalty"]
    if rep_diff < 0:
        return True
    elif rep_diff > 0:
        return False
        
    # 2. jumlah sumber protein unik lebih tinggi
    if new_details["unique_protein_count"] > best_details["unique_protein_count"]:
        return True
    elif new_details["unique_protein_count"] < best_details["unique_protein_count"]:
        return False
        
    # 3. jumlah sayuran unik lebih tinggi
    if new_details["unique_vegetable_count"] > best_details["unique_vegetable_count"]:
        return True
    elif new_details["unique_vegetable_count"] < best_details["unique_vegetable_count"]:
        return False
        
    # 4. kecukupan energi lebih dekat target (absolute deviation from 100)
    new_energy_dev = abs(100.0 - new_details["energy_adequacy"])
    best_energy_dev = abs(100.0 - best_details["energy_adequacy"])
    if new_energy_dev < best_energy_dev:
        return True
    elif new_energy_dev > best_energy_dev:
        return False
        
    # 5. biaya lebih rendah jika faktor lain sama
    if new_details["total_cost"] < best_details["total_cost"]:
        return True
    elif new_details["total_cost"] > best_details["total_cost"]:
        return False
        
    # fallback to deterministic check to keep test stable
    return score_diff > 0


def normalize_legacy_consultation_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalizer untuk riwayat konsultasi lama agar dapat dibaca dengan aman."""
    import copy
    res = copy.deepcopy(payload)
    totals = res.get("totals", {})
    profile = res.get("profile", {})
    
    if "energy_ratio_percent" not in totals:
        target = totals.get("energy_target") or (profile.get("energy_target", {}).get("target") if isinstance(profile.get("energy_target"), dict) else None)
        energy = totals.get("energy")
        if target and energy:
            totals["energy_ratio_percent"] = round((energy / target * 100), 1)
        else:
            totals["energy_ratio_percent"] = 0.0
            
    if "protein_ratio_percent" not in totals:
        target = totals.get("protein_target")
        protein = totals.get("protein")
        if target and protein:
            totals["protein_ratio_percent"] = round((protein / target * 100), 1)
        else:
            totals["protein_ratio_percent"] = 0.0

    res["totals"] = totals
    return res


def serialize_consultation_for_admin(result: dict[str, Any]) -> dict[str, Any]:
    """Mengembalikan payload konsultasi lengkap tanpa modifikasi untuk admin/dev."""
    return result


def serialize_consultation_for_user(result: dict[str, Any]) -> dict[str, Any]:
    """Mengembalikan payload konsultasi yang aman untuk pengguna biasa."""
    import copy
    res = copy.deepcopy(result)
    
    # Sembunyikan debug candidates ditolak
    res.pop("rejected_candidates_debug", None)
    
    # Bersihkan plan
    if "plan" in res:
        for item in res["plan"]:
            item.pop("fired_rules", None)
            if "fuzzy" in item:
                item["fuzzy"].pop("memberships", None)
                item["fuzzy"].pop("active_rules", None)
                
    # Bersihkan alternatif
    if "alternatives" in res:
        for meal_type in res["alternatives"]:
            for item in res["alternatives"][meal_type]:
                item.pop("fired_rules", None)
                if "fuzzy" in item:
                    item["fuzzy"].pop("memberships", None)
                    item["fuzzy"].pop("active_rules", None)
                    
    # Sembunyikan denda/penalti detail & skor internal dari pengguna umum
    if "combination_analysis" in res:
        analysis = res["combination_analysis"]
        div_score = analysis.get("diversity_score", 0.0)
        
        if div_score >= 85:
            div_text = "Sangat Beragam"
        elif div_score >= 60:
            div_text = "Cukup Beragam"
        else:
            div_text = "Kurang Beragam"
            
        res["combination_analysis_safe"] = {
            "energy_ratio_percent": analysis.get("energy_ratio_percent", res.get("totals", {}).get("energy_ratio_percent", 0.0)),
            "protein_ratio_percent": analysis.get("protein_ratio_percent", res.get("totals", {}).get("protein_ratio_percent", 0.0)),
            "diversity_label": div_text,
            "diversity_score_label": div_text,
            "variation_warning": analysis.get("variation_warning", False),
            "variation_message": analysis.get("variation_message"),
            "status": res.get("status", ""),
            "status_message": res.get("message", ""),
        }
        res.pop("combination_analysis", None)
        
    return res

