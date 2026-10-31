"""Compare local FakeProvider latency with the authenticated gateway endpoint."""

import argparse
import asyncio
import os
import statistics
import time

import httpx

from app.providers.fake import FakeProvider
from app.schemas.chat import ChatCompletionRequest, ChatMessage


def percentile(samples: list[float], quantile: float) -> float:
    """Return a nearest-rank percentile in milliseconds."""
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * quantile)))
    return ordered[index]


async def benchmark(iterations: int, base_url: str, api_key: str) -> None:
    """Measure direct FakeProvider and HTTP gateway calls using one request shape."""
    request = ChatCompletionRequest(
        model="demo-failover", messages=[ChatMessage(role="user", content="benchmark")]
    )
    direct = FakeProvider()
    async with httpx.AsyncClient(timeout=120) as client:
        results: dict[str, list[float]] = {"Direct FakeProvider": [], "Gateway": []}
        for _ in range(iterations):
            started = time.perf_counter()
            await direct.chat(request)
            results["Direct FakeProvider"].append((time.perf_counter() - started) * 1000)
        for _ in range(iterations):
            started = time.perf_counter()
            response = await client.post(
                f"{base_url.rstrip('/')}/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=request.model_dump(exclude_none=True),
            )
            response.raise_for_status()
            results["Gateway"].append((time.perf_counter() - started) * 1000)

    direct_median = statistics.median(results["Direct FakeProvider"])
    print("| Path | RPS | p50 (ms) | p95 (ms) | p99 (ms) |")
    print("| --- | ---: | ---: | ---: | ---: |")
    for label, samples in results.items():
        total_seconds = sum(samples) / 1000
        print(
            f"| {label} | {len(samples) / total_seconds:.2f} | "
            f"{statistics.median(samples):.2f} | {percentile(samples, 0.95):.2f} | "
            f"{percentile(samples, 0.99):.2f} |"
        )
    gateway_median = statistics.median(results["Gateway"])
    print(f"\nGateway median overhead vs direct: {gateway_median - direct_median:.2f} ms")


def main() -> None:
    """Parse benchmark options and run the async measurements."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--base-url", default=os.getenv("BASE_URL", "http://localhost:8000"))
    args = parser.parse_args()
    api_key = os.getenv("API_KEY")
    if not api_key:
        parser.error("Set API_KEY to a team key created through the admin API")
    if args.iterations < 1:
        parser.error("--iterations must be positive")
    asyncio.run(benchmark(args.iterations, args.base_url, api_key))


if __name__ == "__main__":
    main()
