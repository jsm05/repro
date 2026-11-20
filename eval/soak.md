# Soak test: `nvidia/mistral-large-4` (2026-11-20T18:45+00:00)

- Status: finished: stopped at time limit
- Plan: up to 500 requests, time limit 5 h
- Requests: 275; succeeded: 275 (100.0%); last request started at 4.97 h
- Latency p50 35.3 s, p95 154.9 s, max 300.0 s
- Errors by class: none
- 429 responses: 0
- Timeouts or connection errors: 0
- Replies cut off at max_tokens (finish_reason=length): 0
- Mean reasoning tokens per reply: 412

| Size | Count | OK | p50 s |
|---|---|---|---|
| small | 69 | 69 | 22.2 |
| medium | 138 | 138 | 37.3 |
| large | 68 | 68 | 46.4 |

| Hour | Requests | OK | 429s | Other errors | p50 s |
|---|---|---|---|---|---|
| 0 | 38 | 38 | 0 | 0 | 68.6 |
| 1 | 57 | 57 | 0 | 0 | 39.0 |
| 2 | 53 | 53 | 0 | 0 | 41.7 |
| 3 | 58 | 58 | 0 | 0 | 26.2 |
| 4 | 69 | 69 | 0 | 0 | 26.3 |
