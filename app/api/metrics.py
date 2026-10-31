"""Prometheus scrape endpoint."""

from fastapi import APIRouter, Response

from app.observability.metrics import render_metrics

router = APIRouter(tags=["observability"])


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Expose current gateway counters and gauges in Prometheus format."""
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)
