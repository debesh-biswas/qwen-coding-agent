**CONSTRAINT: Your final deliverable is a file named `REPORT.json` in the current directory, containing one JSON
object with exactly the 12 keys listed at the end of this task. Write it before you finish.**

# Incident: checkout failure for order ORD-77341

At 2026-09-17 14:32 UTC a customer's checkout for order `ORD-77341` failed. The browser received:

```
HTTP 502 Bad Gateway  (POST /api/checkout, order ORD-77341)
```

Logs from the four services involved are in `logs/`:

- `logs/gateway.log` – API gateway
- `logs/orders.log` – orders service
- `logs/inventory.log` – inventory service
- `logs/payments.log` – payments service

Each log is large (roughly 15,000–25,000 tokens) and noisy, and contains many unrelated errors. Services do not
log each other's IDs: each service tags its lines with its own identifiers, and a line in one service tells you
which identifier to look for in the next. Follow this specific failed request from the gateway to the component
that actually caused it.

## REPORT.json

Answer each question below with the value exactly as it appears in the logs. Use JSON strings for IDs, names,
paths and timestamps, and JSON integers for counts and numbers.

1. `gateway_request_id` — the gateway's request ID (`req=`) of the failed POST /api/checkout for ORD-77341.
2. `upstream_trace_id` — the trace ID the gateway recorded for the upstream call of that request.
3. `hold_id` — the inventory hold that the orders service was waiting on for that trace.
4. `worker` — the inventory worker that hold was queued on.
5. `root_cause_component` — the software component that caused the failure (a class name, not a service or worker
   name), named exactly as it is spelled in the logs.
6. `payment_intent_id` — the payment intent created for ORD-77341.
7. `gateway_status` — the HTTP status code the gateway logged when it answered that request (integer).
8. `upstream_timeout_ms` — the timeout in milliseconds the gateway recorded for the upstream call of that request (integer).
9. `hold_wait_ms` — how many milliseconds the orders service reports it had waited for that hold when it gave up (integer).
10. `root_cause_exception` — the exception class that terminated the task of the root-cause component which blocked the hold, as logged.
11. `first_failure_timestamp` — the timestamp, exactly as written in `logs/inventory.log`, of the first WARN or ERROR line logged by
    the task that blocked the hold.
12. `blocked_holds_count` — the number of holds that the blocking task's ERROR line reports as blocked (integer).
