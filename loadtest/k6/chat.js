import http from 'k6/http';
import { check } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const failures = new Rate('gateway_client_errors');
const latency = new Trend('gateway_latency', true);
const baseUrl = __ENV.BASE_URL || 'http://localhost:8000';

export const options = {
  scenarios: {
    steady: {
      executor: __ENV.SCENARIO === 'burst' ? 'ramping-vus' : 'constant-vus',
      vus: Number(__ENV.VUS || 10),
      duration: __ENV.DURATION || '2m',
      stages: __ENV.SCENARIO === 'burst'
        ? [{ duration: '15s', target: 10 }, { duration: '10s', target: 100 },
          { duration: '30s', target: 100 }, { duration: '15s', target: 0 }]
        : undefined,
    },
  },
  thresholds: { gateway_client_errors: ['rate<0.001'] },
};

export default function () {
  const response = http.post(`${baseUrl}/v1/chat/completions`, JSON.stringify({
    model: __ENV.MODEL || 'demo-failover',
    messages: [{ role: 'user', content: 'Load test request' }],
    max_tokens: 24,
  }), {
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${__ENV.API_KEY || ''}`,
    },
  });
  latency.add(response.timings.duration);
  failures.add(response.status >= 400);
  check(response, { 'gateway returned success': (res) => res.status === 200 });
}
