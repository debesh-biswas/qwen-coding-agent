**CONSTRAINT: Your final deliverable is a file named `REPORT.json` in the current directory, containing one JSON
object with exactly the 12 keys listed at the end of this task. Write it before you finish.**

# Incident: address change for order ORD-61209

At 2026-10-21 21:38 UTC a customer's delivery-address change for order `ORD-61209` failed. The client received:

```
HTTP 503 Service Unavailable  (PATCH /api/orders/ORD-61209/address)
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

1. `gateway_request_id` — the gateway's request ID (`rid=`) of the failed PATCH /api/orders/ORD-61209/address.
2. `upstream_trace_id` — the `x-trace-id` the gateway recorded for the upstream call of that request.
3. `lease_id` — the warehouse slot lease the orders service was waiting on for that trace.
4. `worker` — the warehouse agent that lease was queued on.
5. `root_cause_component` — the software component that caused the failure (a class name, not a service or agent name),
   named exactly as it is spelled in the logs.
6. `payment_txn_id` — the payment authorization transaction created for ORD-61209.
7. `status_code` — the HTTP status code the gateway logged when it answered that request (integer).
8. `upstream_timeout_ms` — the upstream timeout in milliseconds the gateway logged for that request (integer).
9. `lease_wait_ms` — how many milliseconds the orders service reports it had waited for the lease when the caller disconnected (integer).
10. `exception_class` — the exception class that made the blocking task fail, as logged.
11. `blocking_task_started_at` — the timestamp, exactly as written in `logs/inventory.log`, of the START line of the task that
    blocked the lease.
12. `queued_leases` — the number of queued leases the blocking task's FAILED line reports (integer).
