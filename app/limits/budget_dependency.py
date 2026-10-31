"""FastAPI dependency for monthly budget operations."""

from fastapi import Request

from app.limits.budget import BudgetManager


def get_budget_manager(request: Request) -> BudgetManager:
    """Return the budget manager initialized during app startup."""
    return request.app.state.budget_manager
