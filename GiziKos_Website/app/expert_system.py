from __future__ import annotations

import json
from dataclasses import dataclass
from math import isfinite
from typing import Any, Callable

from .models import Recipe
from .schemas import ConsultationInput


@dataclass(frozen=True)
class Rule:
    code: str
    priority: int
    description: str
    condition: Callable[[dict[str, Any], set[str]], bool]
    conclusions: tuple[str, ...]


@dataclass
class CandidateResult:
    recipe: Recipe
    facts: set[str]
    fired_rules: list[dict[str, Any]]
    eligible: bool
    rejection_reasons: list[str]
    metrics: dict[str, float]
    score: float = 0.0
    fuzzy_breakdown: dict[str, Any] | None = None


MEAL_BUDGET_SPLITS_3 = {"breakfast": 0.25, "lunch": 0.40, "dinner": 0.35}
MEAL_BUDGET_SPLITS_2 = {"lunch": 0.45, "dinner": 0.55}
ACTIVITY_FACTORS = {"low": 1.35, "medium": 1.55, "high": 1.75}
GOAL_ADJUSTMENTS = {"lose": -300, "gain": 300, "muscle": 250, "balanced": 0, "maintain": 0, "hemat": 0}


def split_values(text: str) -> set[str]:
    return {item.strip() for item in (text or "").split(";") if item.strip()}


def recipe_metrics(recipe: Recipe, portion_multiplier: float = 1.0) -> dict[str, float]:
    metrics = {"energy": 0.0, "protein": 0.0, "fat": 0.0, "carbs": 0.0, "fiber": 0.0, "cost": 0.0}
    for item in recipe.ingredients:
        factor = (item.grams * portion_multiplier) / 100.0
        food = item.food
        metrics["energy"] += food.energy * factor
        metrics["protein"] += food.protein * factor
        metrics["fat"] += food.fat * factor
        metrics["carbs"] += food.carbs * factor
        metrics["fiber"] += food.fiber * factor
        metrics["cost"] += food.price_per_100g * factor
    return {key: round(value, 2) for key, value in metrics.items()}


def estimate_energy_target(user: ConsultationInput) -> dict[str, float]:
    # Estimasi informatif Mifflin-St Jeor, bukan terapi medis.
    sex_constant = 5 if user.gender == "male" else -161
    bmr = 10 * user.weight + 6.25 * user.height - 5 * user.age + sex_constant
    maintenance = bmr * ACTIVITY_FACTORS[user.activity_level]
    adjusted = maintenance + GOAL_ADJUSTMENTS[user.goal]
    minimum = 1500 if user.gender == "male" else 1200
    target = max(minimum, min(3600, adjusted))
    return {"bmr": round(bmr), "maintenance": round(maintenance), "target": round(target)}


def meal_split(user: ConsultationInput, meal_type: str) -> float:
    split = MEAL_BUDGET_SPLITS_3 if user.meal_count == 3 else MEAL_BUDGET_SPLITS_2
    return split.get(meal_type, 0.33)


def build_context(recipe: Recipe, user: ConsultationInput, settings: dict[str, str], portion_multiplier: float = 1.0) -> dict[str, Any]:
    ingredient_slugs = {item.food.slug for item in recipe.ingredients}
    ingredient_names = {item.food.name.lower() for item in recipe.ingredients}
    recipe_allergens: set[str] = set()
    vegetarian = True
    for item in recipe.ingredients:
        recipe_allergens |= split_values(item.food.allergens)
        if "vegetarian" not in split_values(item.food.tags):
            vegetarian = False

    tools_required = split_values(recipe.tools)
    available_tools = set(user.tools)
    missing_tools = tools_required - available_tools

    metrics = recipe_metrics(recipe, portion_multiplier)
    budget_for_meal = user.daily_budget * meal_split(user, recipe.meal_type)
    available = set(user.available_ingredients)
    coverage = len(ingredient_slugs & available) / max(len(ingredient_slugs), 1)
    disliked = {item.lower().strip() for item in user.disliked_ingredients}
    disliked_hit = {slug for slug in ingredient_slugs if slug.lower() in disliked} | {name for name in ingredient_names if name in disliked}

    return {
        "recipe": recipe,
        "user": user,
        "settings": settings,
        "ingredients": ingredient_slugs,
        "allergens": recipe_allergens,
        "vegetarian_recipe": vegetarian,
        "missing_tools": missing_tools,
        "metrics": metrics,
        "budget_for_meal": budget_for_meal,
        "coverage": coverage,
        "disliked_hit": disliked_hit,
    }


def build_rules() -> list[Rule]:
    return sorted(
        [
            Rule("R001", 100, "Usia di luar target utama mahasiswa kos.", lambda c, f: c["user"].age < int(c["settings"]["age_min"]) or c["user"].age > int(c["settings"]["age_max"]), ("reject_scope",)),
            Rule("R002", 100, "Kondisi medis khusus memerlukan konsultasi profesional.", lambda c, f: c["settings"].get("medical_scope_block", "true") == "true" and c["user"].has_medical_condition, ("reject_medical",)),
            Rule("R003", 99, "Tidak ada alat masak yang dipilih.", lambda c, f: not c["user"].tools, ("reject_no_tools",)),
            Rule("R010", 95, "Resep mengandung alergen yang dipilih pengguna.", lambda c, f: bool(set(c["user"].allergies) & c["allergens"]), ("reject_allergy",)),
            Rule("R020", 90, "Resep nonvegetarian tidak sesuai preferensi pengguna.", lambda c, f: c["user"].vegetarian and not c["vegetarian_recipe"], ("reject_vegetarian",)),
            Rule("R021", 88, "Resep mengandung bahan yang tidak disukai.", lambda c, f: bool(c["disliked_hit"]), ("reject_disliked",)),
            Rule("R030", 85, "Peralatan memasak yang dibutuhkan tidak tersedia.", lambda c, f: bool(c["missing_tools"]), ("reject_tools",)),
            Rule("R040", 80, "Waktu memasak melebihi batas pengguna.", lambda c, f: c["recipe"].cooking_time > c["user"].max_cooking_time, ("reject_time",)),
            Rule("R050", 75, "Biaya resep melebihi alokasi anggaran waktu makan.", lambda c, f: c["metrics"]["cost"] > c["budget_for_meal"] * float(c["settings"].get("budget_tolerance", "1.0")), ("reject_budget",)),
            Rule("R060", 50, "Cakupan bahan tersedia tinggi.", lambda c, f: c["coverage"] >= 0.75, ("coverage_high",)),
            Rule("R061", 50, "Cakupan bahan tersedia sedang.", lambda c, f: float(c["settings"].get("min_ingredient_coverage", "0.20")) <= c["coverage"] < 0.75, ("coverage_medium",)),
            Rule("R062", 50, "Cakupan bahan tersedia rendah.", lambda c, f: c["coverage"] < float(c["settings"].get("min_ingredient_coverage", "0.20")), ("coverage_low",)),
            Rule("R070", 45, "Resep memiliki sumber protein yang memadai.", lambda c, f: c["metrics"]["protein"] >= 15, ("protein_ok",)),
            Rule("R071", 45, "Resep memiliki serat yang cukup baik.", lambda c, f: c["metrics"]["fiber"] >= 4, ("fiber_ok",)),
            Rule("R080", 10, "Resep lolos seluruh batas wajib.", lambda c, f: not any(item.startswith("reject_") for item in f), ("eligible",)),
        ],
        key=lambda rule: rule.priority,
        reverse=True,
    )


def run_forward_chaining(recipe: Recipe, user: ConsultationInput, settings: dict[str, str], portion_multiplier: float = 1.0) -> CandidateResult:
    context = build_context(recipe, user, settings, portion_multiplier)
    facts: set[str] = set()
    fired: list[dict[str, Any]] = []
    fired_codes: set[str] = set()

    changed = True
    while changed:
        changed = False
        for rule in build_rules():
            if rule.code in fired_codes:
                continue
            if rule.condition(context, facts):
                new_facts = set(rule.conclusions) - facts
                if new_facts:
                    facts.update(new_facts)
                    fired.append({"code": rule.code, "priority": rule.priority, "description": rule.description, "conclusions": list(rule.conclusions)})
                    changed = True
                fired_codes.add(rule.code)

    rejection_map = {
        "reject_scope": "Usia berada di luar target utama 18–25 tahun.",
        "reject_medical": "Kondisi medis khusus berada di luar ruang lingkup rekomendasi umum.",
        "reject_no_tools": "Tidak ada alat masak yang dipilih.",
        "reject_allergy": "Menu mengandung alergen yang harus dihindari.",
        "reject_vegetarian": "Menu tidak sesuai preferensi vegetarian.",
        "reject_disliked": "Menu mengandung bahan yang tidak disukai.",
        "reject_tools": "Peralatan memasak belum tersedia.",
        "reject_time": "Waktu memasak melebihi batas.",
        "reject_budget": "Biaya menu melebihi anggaran.",
    }
    rejections = [message for fact, message in rejection_map.items() if fact in facts]
    res = CandidateResult(recipe=recipe, facts=facts, fired_rules=fired, eligible="eligible" in facts and not rejections, rejection_reasons=rejections, metrics=context["metrics"])
    res.portion_multiplier = portion_multiplier
    return res


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def left_shoulder(x: float, full_until: float, zero_at: float) -> float:
    if x <= full_until:
        return 1.0
    if x >= zero_at:
        return 0.0
    return (zero_at - x) / (zero_at - full_until)


def right_shoulder(x: float, zero_until: float, full_at: float) -> float:
    if x <= zero_until:
        return 0.0
    if x >= full_at:
        return 1.0
    return (x - zero_until) / (full_at - zero_until)


def triangle(x: float, left: float, peak: float, right: float) -> float:
    if x <= left or x >= right:
        return 0.0
    if x == peak:
        return 1.0
    if x < peak:
        return (x - left) / (peak - left)
    return (right - x) / (right - peak)


def fuzzy_score(candidate: CandidateResult, user: ConsultationInput, settings: dict[str, str]) -> dict[str, Any]:
    # Metode Defuzzifikasi: Fuzzy Sugeno orde nol (weighted average berbasis singleton output)
    recipe = candidate.recipe
    metrics = candidate.metrics
    budget = user.daily_budget * meal_split(user, recipe.meal_type)
    budget_ratio = metrics["cost"] / max(budget, 1)
    time_ratio = recipe.cooking_time / max(user.max_cooking_time, 1)
    ingredient_slugs = {item.food.slug for item in recipe.ingredients}
    coverage = len(ingredient_slugs & set(user.available_ingredients)) / max(len(ingredient_slugs), 1)

    memberships = {
        "budget_fit": {
            "low": left_shoulder(budget_ratio, 0.60, 0.90),
            "medium": triangle(budget_ratio, 0.60, 0.85, 1.10),
            "high": right_shoulder(budget_ratio, 0.90, 1.20),
        },
        "time_fit": {
            "low": left_shoulder(time_ratio, 0.45, 0.80),
            "medium": triangle(time_ratio, 0.45, 0.75, 1.05),
            "high": right_shoulder(time_ratio, 0.85, 1.15),
        },
        "coverage": {
            "low": left_shoulder(coverage, 0.20, 0.45),
            "medium": triangle(coverage, 0.25, 0.60, 0.85),
            "high": right_shoulder(coverage, 0.60, 0.85),
        },
    }

    fuzzy_rules = [
        ("F01", min(memberships["budget_fit"]["low"], memberships["time_fit"]["low"], memberships["coverage"]["high"]), 95),
        ("F02", min(memberships["budget_fit"]["low"], memberships["coverage"]["medium"]), 84),
        ("F03", min(memberships["budget_fit"]["medium"], memberships["time_fit"]["low"]), 79),
        ("F04", min(memberships["budget_fit"]["medium"], memberships["coverage"]["medium"]), 72),
        ("F05", max(memberships["budget_fit"]["high"], memberships["time_fit"]["high"]), 42),
        ("F06", memberships["coverage"]["low"], 55),
    ]
    active_rules = [{"code": code, "strength": round(strength, 4), "output": output} for code, strength, output in fuzzy_rules if strength > 0]
    denominator = sum(item["strength"] for item in active_rules)
    fuzzy_value = sum(item["strength"] * item["output"] for item in active_rules) / denominator if denominator else 50.0

    energy_target = estimate_energy_target(user)
    nutrition = nutrition_balance_score(metrics, recipe.meal_type, energy_target["target"], user.meal_count)
    goal_score = goal_compatibility_score(metrics, user, recipe.meal_type, energy_target["target"])
    final_score = clamp((0.58 * fuzzy_value + 0.27 * nutrition + 0.15 * goal_score) / 100.0) * 100
    if not isfinite(final_score):
        final_score = 0.0
    return {
        "score": round(final_score, 1),
        "fuzzy_value": round(fuzzy_value, 1),
        "nutrition_score": round(nutrition, 1),
        "goal_score": round(goal_score, 1),
        "coverage": round(coverage * 100, 1),
        "budget_ratio": round(budget_ratio, 3),
        "time_ratio": round(time_ratio, 3),
        "memberships": memberships,
        "active_rules": active_rules,
    }


def nutrition_balance_score(metrics: dict[str, float], meal_type: str, daily_target: float | None = None, meal_count: int = 3) -> float:
    if daily_target:
        split = MEAL_BUDGET_SPLITS_3 if meal_count == 3 else MEAL_BUDGET_SPLITS_2
        target = daily_target * split.get(meal_type, 0.33)
        low, high = target * 0.72, target * 1.18
    else:
        defaults = {"breakfast": (350, 650), "lunch": (500, 850), "dinner": (400, 750)}
        low, high = defaults.get(meal_type, (400, 800))
    energy = metrics["energy"]
    if low <= energy <= high:
        energy_score = 100.0
    elif energy < low:
        energy_score = clamp(energy / max(low, 1)) * 100
    else:
        energy_score = clamp(1 - (energy - high) / max(high, 1)) * 100
    protein_score = clamp(metrics["protein"] / 22.0) * 100
    fiber_score = clamp(metrics["fiber"] / 6.0) * 100
    fat_score = 100.0 if metrics["fat"] <= 25 else clamp(1 - (metrics["fat"] - 25) / 30) * 100
    return 0.38 * energy_score + 0.30 * protein_score + 0.22 * fiber_score + 0.10 * fat_score


def goal_compatibility_score(metrics: dict[str, float], user: ConsultationInput, meal_type: str, daily_target: float) -> float:
    target = daily_target * meal_split(user, meal_type)
    energy_ratio = metrics["energy"] / max(target, 1)
    energy_fit = clamp(1 - abs(1 - energy_ratio) / 0.55) * 100
    if user.goal == "lose":
        # 0.85–1.05 -> skor tinggi
        # 0.75–0.85 -> skor sedang (linear interpolation)
        # < 0.75 -> penalti karena terlalu rendah
        # > 1.05 -> skor menurun
        if 0.85 <= energy_ratio <= 1.05:
            energy_score = 100.0
        elif 0.75 <= energy_ratio < 0.85:
            energy_score = 70.0 + (energy_ratio - 0.75) / 0.10 * 30.0
        elif energy_ratio < 0.75:
            energy_score = clamp((energy_ratio - 0.40) / 0.35) * 70.0
        else: # energy_ratio > 1.05
            energy_score = clamp((1.50 - energy_ratio) / 0.45) * 100.0
            
        return 0.55 * energy_score + 0.30 * clamp(metrics["fiber"] / 7.0) * 100.0 + 0.15 * clamp(metrics["protein"] / 22.0) * 100.0
    if user.goal == "gain":
        return 0.60 * clamp(energy_ratio / 1.05) * 100 + 0.25 * clamp(metrics["protein"] / 22) * 100 + 0.15 * clamp(metrics["carbs"] / 90) * 100
    if user.goal == "muscle":
        # Target protein harian prototipe: 1.6 gram per kg berat badan (bukan program atlet profesional).
        daily_protein_target = 1.6 * user.weight
        meal_protein_target = daily_protein_target * meal_split(user, meal_type)
        protein_fit = clamp(metrics["protein"] / max(meal_protein_target, 1.0)) * 100
        return 0.50 * protein_fit + 0.35 * energy_fit + 0.15 * clamp(metrics["carbs"] / 90) * 100
    if user.goal == "hemat":
        return 0.60 * clamp(1.10 - (metrics["cost"] / max(user.daily_budget * meal_split(user, meal_type), 1))) * 100 + 0.40 * energy_fit
    if user.goal == "balanced":
        # Balanced goal considers energy balance, protein sufficiency, and fiber content
        protein_fit = clamp(metrics["protein"] / 20.0) * 100.0
        fiber_fit = clamp(metrics["fiber"] / 5.0) * 100.0
        return 0.40 * energy_fit + 0.35 * protein_fit + 0.25 * fiber_fit
    if user.goal == "maintain":
        # Maintain goal strictly emphasizes precise energy maintenance stability and basic protein maintenance
        strict_energy_fit = clamp(1.0 - abs(1.0 - energy_ratio) / 0.35) * 100.0
        protein_min_fit = clamp(metrics["protein"] / 15.0) * 100.0
        return 0.70 * strict_energy_fit + 0.30 * protein_min_fit
    return energy_fit



def serialize_recipe(candidate: CandidateResult, fuzzy: dict[str, Any], available: set[str]) -> dict[str, Any]:
    recipe = candidate.recipe
    mult = getattr(candidate, "portion_multiplier", 1.0)
    ingredients = [
        {
            "slug": item.food.slug,
            "name": item.food.name,
            "grams": round(item.grams * mult, 1),
            "available": item.food.slug in available,
            "category": item.food.category,
        }
        for item in recipe.ingredients
    ]
    return {
        "id": recipe.slug,
        "name": recipe.name,
        "meal_type": recipe.meal_type,
        "cooking_time": recipe.cooking_time,
        "tools": sorted(split_values(recipe.tools)),
        "tags": sorted(split_values(recipe.tags)),
        "steps": json.loads(recipe.steps_json),
        "ingredients": ingredients,
        "nutrition": candidate.metrics,
        "score": fuzzy["score"],
        "fuzzy": fuzzy,
        "reasons": build_reasons(candidate, fuzzy),
        "fired_rules": candidate.fired_rules,
        "portion_multiplier": mult,
    }


def build_reasons(candidate: CandidateResult, fuzzy: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if fuzzy["coverage"] >= 75:
        reasons.append("Sebagian besar bahan sudah tersedia di kos.")
    elif fuzzy["coverage"] >= 40:
        reasons.append("Sebagian bahan tersedia sehingga daftar belanja tetap ringkas.")
    else:
        reasons.append("Menu menambah variasi, tetapi memerlukan beberapa bahan belanja.")
    if fuzzy["budget_ratio"] <= 0.85:
        reasons.append("Biaya berada aman di bawah alokasi anggaran waktu makan.")
    if fuzzy["goal_score"] >= 75:
        reasons.append("Komposisi menu selaras dengan tujuan utama yang dipilih.")
    if candidate.metrics["protein"] >= 15:
        reasons.append("Mengandung sumber protein yang memadai untuk satu kali makan.")
    if candidate.metrics["fiber"] >= 4:
        reasons.append("Kandungan serat relatif baik dari sayur, buah, atau pangan berserat.")
    if candidate.recipe.cooking_time <= 15:
        reasons.append("Waktu memasak singkat dan sesuai ritme mahasiswa.")
    return reasons[:4]


def get_cooking_methods(recipe_name: str, steps_json: str) -> set[str]:
    methods = set()
    name_lower = recipe_name.lower()
    steps_lower = steps_json.lower()
    for m in ["tumis", "rebus", "goreng", "kukus", "panggang", "bakar", "saji", "campur"]:
        if m in name_lower or m in steps_lower:
            methods.add(m)
    return methods


def evaluate_daily_combinations(
    combo: list[CandidateResult],
    user: ConsultationInput,
    settings: dict[str, str],
    weights: dict[str, float]
) -> dict[str, Any]:
    total_energy = sum(res.metrics["energy"] for res in combo)
    total_protein = sum(res.metrics["protein"] for res in combo)
    total_cost = sum(res.metrics["cost"] for res in combo)
    
    # 1. Fuzzy score average
    avg_fuzzy = sum(res.score for res in combo) / len(combo)
    
    # 2. Energy adequacy score
    energy_target = estimate_energy_target(user)["target"]
    energy_ratio = total_energy / max(energy_target, 1.0)
    energy_adequacy = clamp(1.0 - abs(1.0 - energy_ratio) / 0.5) * 100
    
    # 3. Protein adequacy score
    factor = 1.6 if user.goal == "muscle" else 1.0
    protein_target = user.weight * factor
    protein_ratio = total_protein / max(protein_target, 1.0)
    protein_adequacy = clamp(protein_ratio) * 100
    
    # 4. Budget score
    budget = user.daily_budget
    if total_cost <= budget:
        if user.goal == "hemat":
            budget_score = clamp(1.0 - total_cost / budget) * 100
        else:
            budget_score = 100.0
    else:
        budget_score = clamp(1.0 - (total_cost - budget) / max(budget * 0.2, 1.0)) * 100
        
    # 5. Stock score
    available_set = set(user.available_ingredients)
    total_ings = set()
    owned_ings = set()
    for res in combo:
        for ing in res.recipe.ingredients:
            total_ings.add(ing.food.slug)
            if ing.food.slug in available_set:
                owned_ings.add(ing.food.slug)
    stock_score = (len(owned_ings) / len(total_ings) * 100) if total_ings else 0.0
    
    # 6. Goal compatibility score average
    avg_goal = sum(goal_compatibility_score(res.metrics, user, res.recipe.meal_type, energy_target) for res in combo) / len(combo)
    
    # 7. Diversity score (starting at 100, subtracting repetition penalties)
    protein_penalty = 0.0
    carb_penalty = 0.0
    veg_penalty = 0.0
    method_penalty = 0.0
    
    proteins = [res.recipe.main_protein for res in combo if res.recipe.main_protein]
    carbs = [res.recipe.main_carbohydrate for res in combo if res.recipe.main_carbohydrate]
    vegetable_groups = [res.recipe.vegetable_group for res in combo if res.recipe.vegetable_group]
    cooking_methods = []
    for res in combo:
        methods = get_cooking_methods(res.recipe.name, res.recipe.steps_json)
        cooking_methods.extend(methods)
        
    # Calculate protein penalties
    if len(proteins) >= 3:
        if len(set(proteins)) == 1:
            protein_penalty += 35.0
        elif len(set(proteins)) == 2:
            protein_penalty += 15.0
    elif len(proteins) == 2:
        if len(set(proteins)) == 1:
            protein_penalty += 15.0
            
    # Calculate carbohydrate penalties
    if len(carbs) >= 3:
        if len(set(carbs)) == 1:
            carb_penalty += 15.0
        elif len(set(carbs)) == 2:
            carb_penalty += 5.0
    elif len(carbs) == 2:
        if len(set(carbs)) == 1:
            carb_penalty += 5.0
            
    # Calculate vegetable penalties (if same vegetable used in multiple meals)
    all_vegs = []
    for vg in vegetable_groups:
        if vg and vg != "none":
            all_vegs.extend(vg.split(";"))
    all_vegs = [v for v in all_vegs if v and v != "none"]
    veg_counts = {}
    for v in all_vegs:
        veg_counts[v] = veg_counts.get(v, 0) + 1
    for count in veg_counts.values():
        if count > 1:
            veg_penalty += (count - 1) * 10.0
            
    # Calculate cooking method penalties
    method_counts = {}
    for m in cooking_methods:
        method_counts[m] = method_counts.get(m, 0) + 1
    for m, count in method_counts.items():
        if count >= 3:
            method_penalty += 15.0
        elif count == 2:
            method_penalty += 5.0
 
    repetition_penalty = protein_penalty + carb_penalty + veg_penalty + method_penalty
    diversity_score = max(0.0, 100.0 - repetition_penalty)
    
    # Calculate combination score
    weighted_score = (
        weights.get("fuzzy", 0.30) * avg_fuzzy +
        weights.get("energy_adequacy", 0.20) * energy_adequacy +
        weights.get("protein_adequacy", 0.15) * protein_adequacy +
        weights.get("budget", 0.10) * budget_score +
        weights.get("stock", 0.10) * stock_score +
        weights.get("goal", 0.10) * avg_goal +
        weights.get("diversity", 0.05) * diversity_score
    )
    
    combination_score = weighted_score  # Pilihan A: Jangan kurangi lagi denda repetisi dari skor final
    
    unique_proteins = set(proteins)
    unique_protein_count = len(unique_proteins)
    unique_vegetable_count = len(set(all_vegs))
    
    goal_components = {
        res.recipe.meal_type: round(goal_compatibility_score(res.metrics, user, res.recipe.meal_type, energy_target), 1)
        for res in combo
    }

    return {
        "score": round(max(0.0, combination_score), 1),
        "avg_fuzzy": round(avg_fuzzy, 1),
        "energy_adequacy": round(energy_adequacy, 1),
        "protein_adequacy": round(protein_adequacy, 1),
        "budget_score": round(budget_score, 1),
        "stock_score": round(stock_score, 1),
        "avg_goal": round(avg_goal, 1),
        "goal_component_scores": goal_components,
        "diversity_score": round(diversity_score, 1),
        "protein_repetition_penalty": round(protein_penalty, 1),
        "carbohydrate_repetition_penalty": round(carb_penalty, 1),
        "vegetable_repetition_penalty": round(veg_penalty, 1),
        "method_repetition_penalty": round(method_penalty, 1),
        "total_repetition_penalty": round(repetition_penalty, 1),
        "unique_protein_count": unique_protein_count,
        "unique_vegetable_count": unique_vegetable_count,
        "total_energy": round(total_energy, 2),
        "total_protein": round(total_protein, 2),
        "total_cost": round(total_cost, 2),
    }

