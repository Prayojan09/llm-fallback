"""Team usage summary endpoint."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.team import authenticate_team
from app.db.models import UsageLedger
from app.db.session import get_db
from app.limits.budget import BudgetManager
from app.limits.budget_dependency import get_budget_manager
from app.schemas.teams import TeamAuth

router = APIRouter(prefix="/v1", tags=["usage"])


class UsageSummary(BaseModel):
    """Monthly usage and budget totals for the authenticated team."""

    month: str
    request_count: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    reserved_usd: float
    monthly_budget_usd: float
    budget_used_ratio: float


@router.get("/usage", response_model=UsageSummary)
async def usage_summary(
    request: Request,
    team: TeamAuth = Depends(authenticate_team),
    session: AsyncSession = Depends(get_db),
    budget_manager: BudgetManager = Depends(get_budget_manager),
) -> UsageSummary:
    """Return current-month ledger totals and Redis budget counters."""
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    totals = await session.execute(
        select(
            func.count(UsageLedger.id),
            func.coalesce(func.sum(UsageLedger.input_tokens), 0),
            func.coalesce(func.sum(UsageLedger.output_tokens), 0),
            func.coalesce(func.sum(UsageLedger.cost_usd), 0.0),
        ).where(UsageLedger.team_id == team.id, UsageLedger.created_at >= month_start)
    )
    count, input_tokens, output_tokens, ledger_cost = totals.one()
    try:
        used, reserved = await budget_manager.usage(team.id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Budget store is unavailable") from exc
    cost = max(float(ledger_cost), used)
    ratio = cost / team.monthly_budget_usd if team.monthly_budget_usd > 0 else 0.0
    return UsageSummary(
        month=now.strftime("%Y-%m"),
        request_count=int(count),
        input_tokens=int(input_tokens),
        output_tokens=int(output_tokens),
        cost_usd=cost,
        reserved_usd=reserved,
        monthly_budget_usd=team.monthly_budget_usd,
        budget_used_ratio=ratio,
    )
