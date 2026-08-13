"""TheMealDB client — Indian dish search + full recipe lookup.

Free, no API key (the shared public test key `1`). Every function returns
empty-on-failure and logs a warning rather than raising, so the graph nodes stay
simple and a flaky network never crashes a request.

Note: TheMealDB's `filter.php?a=Indian` endpoint is unreliable on the free test
key, so we discover dishes via `search.php?s=<keyword>` instead — which does
return Indian dishes with full recipes.
"""
import logging
from typing import Optional

import httpx

logger = logging.getLogger("tools.mealdb")

_BASE = "https://www.themealdb.com/api/json/v1/1"
_client: Optional[httpx.Client] = None


def _http() -> httpx.Client:
    """Lazy module-level client — safe for the FastAPI threadpool and the
    stateless MLflow agent (both call synchronously)."""
    global _client
    if _client is None:
        _client = httpx.Client(timeout=httpx.Timeout(6.0, connect=3.0))
    return _client


def _card(meal: dict) -> dict:
    """A lightweight candidate card (no full recipe)."""
    return {
        "id": meal.get("idMeal"),
        "name": meal.get("strMeal"),
        "thumb": meal.get("strMealThumb"),
        "category": meal.get("strCategory"),
        "area": meal.get("strArea"),
    }


def search_meals(keyword: str, limit: int = 5) -> list[dict]:
    """Search dishes by keyword. Returns up to `limit` candidate cards (may be []).

    Prefers Indian-area matches, then falls back to any match so a reasonable
    dish is always offered when the API has results.
    """
    try:
        resp = _http().get(f"{_BASE}/search.php", params={"s": keyword})
        resp.raise_for_status()
        meals = resp.json().get("meals") or []
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        logger.warning("search_meals(%r) failed: %s", keyword, exc)
        return []

    # TheMealDB labels Indian dishes with strArea "India" (some data says
    # "Indian") — accept either so Indian matches sort to the front.
    def _is_indian(m: dict) -> bool:
        return (m.get("strArea") or "").lower() in ("india", "indian")

    indian = [m for m in meals if _is_indian(m)]
    ranked = indian + [m for m in meals if not _is_indian(m)]
    return [_card(m) for m in ranked[:limit]]


def _normalize_recipe(meal: dict) -> dict:
    """Flatten TheMealDB's strIngredient1..20 / strMeasure1..20 into pairs."""
    ingredients = []
    for i in range(1, 21):
        item = (meal.get(f"strIngredient{i}") or "").strip()
        measure = (meal.get(f"strMeasure{i}") or "").strip()
        if item:
            ingredients.append((item, measure))
    return {
        "id": meal.get("idMeal"),
        "name": meal.get("strMeal"),
        "area": meal.get("strArea"),
        "category": meal.get("strCategory"),
        "instructions": (meal.get("strInstructions") or "").strip(),
        "ingredients": ingredients,
        "thumb": meal.get("strMealThumb"),
        "youtube": meal.get("strYoutube"),
    }


def lookup_meal(meal_id: str) -> Optional[dict]:
    """Full recipe for a meal id. Returns a normalized dict, or None on failure."""
    try:
        resp = _http().get(f"{_BASE}/lookup.php", params={"i": meal_id})
        resp.raise_for_status()
        meals = resp.json().get("meals") or []
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        logger.warning("lookup_meal(%r) failed: %s", meal_id, exc)
        return None
    if not meals:
        return None
    return _normalize_recipe(meals[0])
