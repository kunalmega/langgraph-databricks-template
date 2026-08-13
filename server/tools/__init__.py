"""Deterministic HTTP tools for the cuisine concierge graph.

These are plain functions (not LangChain @tool objects) called directly by the
graph's tool nodes — they make no LLM/AI-Gateway calls. All are free/no-key and
fail gracefully (empty/None) rather than raising.
"""
from .mealdb import lookup_meal, search_meals
from .weather import get_weather

__all__ = ["search_meals", "lookup_meal", "get_weather"]
