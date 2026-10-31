"""Metrics, request IDs, and log privacy checks."""

import json
import logging

from fastapi.testclient import TestClient

from app.observability.logging import JsonFormatter, request_id_var
from tests.test_stage1_api import create_test_app


def test_chat_metrics_and_request_id_are_exposed_without_prompt_labels() -> None:
    """Metrics track safe dimensions and each response echoes a correlation ID."""
    with TestClient(create_test_app()) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"X-Request-ID": "portfolio-check-123"},
            json={
                "model": "fake-model",
                "messages": [{"role": "user", "content": "private prompt text"}],
            },
        )
        scrape = client.get("/metrics")

    assert response.headers["X-Request-ID"] == "portfolio-check-123"
    assert scrape.status_code == 200
    assert "gateway_requests_total" in scrape.text
    assert 'team="test-team"' in scrape.text
    assert "private prompt text" not in scrape.text
    assert "gw_live_" not in scrape.text


def test_json_formatter_includes_request_id_without_exception_details() -> None:
    """Structured records carry correlation while leaving payload fields absent."""
    token = request_id_var.set("request-7")
    try:
        record = logging.LogRecord(
            "app.test", logging.INFO, __file__, 1, "provider_stream_interrupted", (), None
        )
        rendered = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)

    assert rendered["request_id"] == "request-7"
    assert rendered["event"] == "provider_stream_interrupted"
    assert "prompt" not in rendered
    assert "api_key" not in rendered
