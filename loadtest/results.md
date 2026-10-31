# Load test results

No measurements are checked in yet. Results depend on the host, Docker resources,
Redis/Postgres placement, and configured team limits; do not treat local figures as
production capacity claims.

| Scenario | Mode | RPS | p50 | p95 | p99 | Gateway overhead vs direct FakeProvider | Client errors |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Steady | Not run | — | — | — | — | — | — |
| Burst | Not run | — | — | — | — | — | — |
| Failover | Not run | — | — | — | — | — | — |

Run `k6 run loadtest/k6/chat.js` for steady traffic and `SCENARIO=burst k6 run
loadtest/k6/chat.js` for a burst (PowerShell: `$env:SCENARIO="burst"`). Set `BASE_URL`,
`API_KEY`, `MODEL`, `VUS`, and `DURATION` as needed. k6 reports request rate and p50/p90/p95;
its JSON output can be retained with `--summary-export`. Run `python -m
loadtest.benchmark` while the gateway is up to compare direct FakeProvider and gateway
latencies with the same synthetic request. Use `loadtest/demo_failover.ps1` to switch the
primary FakeProvider off and back on under load. Check Grafana at `http://localhost:3000`
for fallback and provider-attempt metrics. Apply migrations and create an authorized team
key before running authenticated load.

The included failover demo uses `demo-failover`, whose primary and backup are independent
FakeProvider instances. Its admin toggle is unavailable in `ENVIRONMENT=production` and
requires the normal admin bearer token.
