"""Generate the dbg_* debugging cases (3 incidents, 4 noisy logs each). Deterministic.
Usage: python3 tests/gen/gen_dbg.py            write tests/cases/dbg_*
       python3 tests/gen/gen_dbg.py --check    follow each chain through the written logs and verify expect.json
"""
import datetime as DT
import json
import os
import random
import re
import shutil
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = os.path.join(os.path.dirname(HERE), "cases")
SV = ("gw", "or", "iv", "pa")
FILES = {"gw": "gateway.log", "or": "orders.log", "iv": "inventory.log", "pa": "payments.log"}

HEAD1 = r'''**CONSTRAINT: Your final deliverable is a file named `REPORT.json` in the current directory, containing one JSON
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

'''


def ts(d, st):
    ms = "%03d" % (d.microsecond // 1000)
    return {"iso": d.strftime("%Y-%m-%dT%H:%M:%S.") + ms + "Z", "java": d.strftime("%Y-%m-%d %H:%M:%S,") + ms,
            "plain": d.strftime("%Y-%m-%d %H:%M:%S.") + ms, "tz": d.strftime("%Y-%m-%dT%H:%M:%S.") + ms + "+00:00"}[st]


def ms(d, x):
    return d + DT.timedelta(milliseconds=x)


HEX = "0123456789abcdef"
ALN = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

# ---- line formats per incident (each service has its own style) ----
F1 = dict(
    g_start='{ts} {h} INFO  req={req} {method} {path} order={order} client={ip} ua="{ua}"',
    g_up='{ts} {h} INFO  req={req} upstream=orders-svc trace={trace} attempt=1 timeout_ms={to}',
    g_done='{ts} {h} {lvl:<5} req={req} status={status} dur_ms={dur} bytes={b}',
    g_msg='{ts} {h} {lvl:<5} {msg}',
    o_begin='{ts} [{th}] INFO  c.shop.orders.CheckoutService - trace={trace} checkout begin items={n}',
    o_req='{ts} [{th}] INFO  c.shop.orders.StockClient - trace={trace} reserve sku={sku} qty={q} hold={hold}',
    o_ok='{ts} [{th}] INFO  c.shop.orders.StockClient - trace={trace} hold={hold} granted in {ms}ms',
    o_abort='{ts} [{th}] WARN  c.shop.orders.StockClient - trace={trace} hold={hold} still pending, upstream closed connection; waited={w}ms',
    o_done='{ts} [{th}] INFO  c.shop.orders.CheckoutService - trace={trace} checkout complete total={amt}',
    o_dup='{ts} [{th}] WARN  c.shop.orders.CheckoutService - trace={trace} rejected: checkout already pending for this cart',
    o_dbfail='{ts} [{th}] ERROR c.shop.orders.OrderRepository - trace={trace} could not obtain connection from pool (active=40 max=40, waiting=17)',
    o_msg='{ts} [{th}] {lvl:<5} c.shop.orders.{cls} - {msg}',
    i_ok='{ts} INFO  inventory-api hold={hold} sku={sku} qty={q} worker={worker} granted wait={ms}ms',
    i_queued='{ts} INFO  inventory-api hold={hold} worker={worker} queued behind task={task} pos={pos}',
    i_exp='{ts} WARN  inventory-api hold={hold} expired: worker={worker} task={task} aborted',
    i_rel='{ts} DEBUG inventory-api hold={hold} released',
    w_start='{ts} INFO  [{worker}] task={task} class={cls} started {extra}',
    w_info='{ts} INFO  [{worker}] task={task} class={cls} {msg}',
    w_ok='{ts} INFO  [{worker}] task={task} class={cls} completed in {ms}ms',
    w_warn='{ts} WARN  [{worker}] task={task} class={cls} {msg}',
    w_err='{ts} ERROR [{worker}] task={task} class={cls} failed: {exc}: {msg} blocked_holds={n}',
    p_create='{ts} pay-core INFO  intent.create id={pi} order_ref={order} amount={amt} currency=USD status=requires_confirmation',
    p_ok='{ts} pay-core INFO  intent.capture id={pi} status=succeeded',
    p_cancel='{ts} pay-core WARN  intent.cancel id={pi} reason=checkout_aborted',
    p_decl='{ts} pay-core WARN  charge.declined intent={pi} order_ref={order} code={reason}',
    p_msg='{ts} pay-core {lvl:<5} {msg}',
)
F2 = dict(
    g_start='{{"ts":"{ts}","host":"{h}","level":"INFO","request_id":"{req}","method":"{method}","path":"{path}","order":"{order}","client":"{ip}"}}',
    g_up='{{"ts":"{ts}","host":"{h}","level":"INFO","request_id":"{req}","upstream":"orders-svc","traceparent":"00-{trace}-{spn}-01","timeout_ms":{to}}}',
    g_done='{{"ts":"{ts}","host":"{h}","level":"{lvl}","request_id":"{req}","status":{status},"duration_ms":{dur},"bytes":{b}}}',
    g_msg='{{"ts":"{ts}","host":"{h}","level":"{lvl}","msg":"{msg}"}}',
    o_begin='ts={ts} level=info svc=orders msg="refund started" trace_id={trace} lines={n}',
    o_req='ts={ts} level=info svc=orders msg="restock requested" trace_id={trace} ticket={hold} sku={sku} qty={q}',
    o_ok='ts={ts} level=info svc=orders msg="restock ticket granted" trace_id={trace} ticket={hold} wait_ms={ms}',
    o_abort='ts={ts} level=warn svc=orders msg="restock ticket still pending, caller gone" trace_id={trace} ticket={hold} waited_ms={w}',
    o_done='ts={ts} level=info svc=orders msg="refund submitted" trace_id={trace} amount={amt}',
    o_dup='ts={ts} level=warn svc=orders msg="duplicate refund request rejected" trace_id={trace}',
    o_dbfail='ts={ts} level=error svc=orders msg="db pool exhausted, refund not persisted" trace_id={trace} active=32 max=32',
    o_msg='ts={ts} level={lvl} svc=orders component={cls} msg="{msg}"',
    i_ok='{ts} [stockd] INFO  ticket={hold} sku={sku} qty={q} worker={worker} granted in {ms}ms',
    i_queued='{ts} [stockd] INFO  ticket={hold} parked worker={worker} awaiting task={task} position={pos}',
    i_exp='{ts} [stockd] WARN  ticket={hold} dropped worker={worker} task={task} failed',
    i_rel='{ts} [stockd] DEBUG ticket={hold} closed',
    w_start='{ts} [{worker}] INFO  task={task} {cls}: started {extra}',
    w_info='{ts} [{worker}] INFO  task={task} {cls}: {msg}',
    w_ok='{ts} [{worker}] INFO  task={task} {cls}: finished in {ms}ms',
    w_warn='{ts} [{worker}] WARN  task={task} {cls}: {msg}',
    w_err='{ts} [{worker}] ERROR task={task} {cls}: {exc}: {msg} (stalled_tickets={n})',
    p_create='{ts} [pay-core] INFO  RefundService - refund.create id={pi} order={order} amount={amt}',
    p_ok='{ts} [pay-core] INFO  RefundService - refund.settle id={pi} status=succeeded',
    p_cancel='{ts} [pay-core] WARN  RefundService - refund.cancel id={pi} reason=caller_timeout',
    p_decl='{ts} [pay-core] WARN  RefundService - refund.rejected id={pi} order={order} code={reason}',
    p_msg='{ts} [pay-core] {lvl:<5} {msg}',
)
F3 = dict(
    g_start='[{ts}] {h} INFO  rid={req} {method} {path} client={ip}',
    g_up='[{ts}] {h} INFO  rid={req} -> orders-svc x-trace-id={trace} timeout={to}ms',
    g_done='[{ts}] {h} {lvl:<5} rid={req} <- {status} in {dur}ms',
    g_msg='[{ts}] {h} {lvl:<5} {msg}',
    o_begin='{ts} INFO  orders.amend [{trace}] address amend started (lines={n})',
    o_req='{ts} INFO  orders.slots [{trace}] re-slot {sku} x{q} -> lease {hold}',
    o_ok='{ts} INFO  orders.slots [{trace}] lease {hold} acquired ({ms} ms)',
    o_abort='{ts} WARN  orders.slots [{trace}] lease {hold} not acquired, caller disconnected after {w} ms',
    o_done='{ts} INFO  orders.amend [{trace}] amend committed total={amt}',
    o_dup='{ts} WARN  orders.amend [{trace}] amend already in progress, rejected',
    o_dbfail='{ts} ERROR orders.repo [{trace}] lock wait timeout exceeded; try restarting transaction',
    o_msg='{ts} {lvl:<5} orders.{cls} {msg}',
    i_ok='{ts} lease-mgr  lease={hold} sku={sku} qty={q} -> {worker} OK ({ms} ms)',
    i_queued='{ts} lease-mgr  lease={hold} -> {worker} QUEUED behind {task} (#{pos})',
    i_exp='{ts} lease-mgr  lease={hold} -> {worker} EXPIRED ({task} failed)',
    i_rel='{ts} lease-mgr  lease={hold} released',
    w_start='{ts} {worker}/{task} {cls} START {extra}',
    w_info='{ts} {worker}/{task} {cls} INFO {msg}',
    w_ok='{ts} {worker}/{task} {cls} DONE in {ms} ms',
    w_warn='{ts} {worker}/{task} {cls} WARN {msg}',
    w_err='{ts} {worker}/{task} {cls} FAILED {exc} - {msg} ; queued_leases={n}',
    p_create='{ts} payments authz.adjust txn={pi} order={order} delta={amt} state=pending',
    p_ok='{ts} payments authz.settle txn={pi} state=settled',
    p_cancel='{ts} payments authz.void txn={pi} reason=amend_aborted',
    p_decl='{ts} payments authz.decline txn={pi} order={order} code={reason}',
    p_msg='{ts} payments {lvl:<5} {msg}',
)

HOSTS = ["gw-edge-1", "gw-edge-2", "gw-edge-3"]
UAS = ["Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4) Safari/605.1", "okhttp/4.12.0",
       "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) Firefox/125.0", "shop-android/8.2.1"]
GW_MSGS = ["tls handshake error from 10.4.2.17: remote error: unknown certificate", "rate limit exceeded for client 203.0.113.9 (429)",
           "config reload ok routes=148", "upstream health check ok orders-svc 3/3 healthy", "upstream recs-svc connect timeout after 1500ms",
           "upstream health check ok payments-svc 2/2 healthy", "client 198.51.100.23 sent malformed Content-Length, closing", "route cache refreshed entries=148 took=12ms",
           "jwt validation failed: token expired (sub=u_104233)", "upstream catalog-svc responded slowly: 1840ms"]
OR_MSGS = [("PromoCodeResolver", "WARN", "promo code SPRING25 expired, ignoring"), ("CartCache", "INFO", "evicted 212 entries (ttl)"),
           ("SchedulerTick", "INFO", "tick completed jobs=3 lag=4ms"), ("TaxRateClient", "WARN", "tax-svc slow response 1210ms"),
           ("MetricsFlusher", "INFO", "flushed 418 samples"), ("ShippingQuoteCache", "INFO", "refreshed 64 zones"),
           ("CartCache", "DEBUG", "hit ratio 0.93"), ("OrderRepository", "INFO", "vacuum hint applied table=orders_archive"),
           ("PromoCodeResolver", "INFO", "loaded 31 active promos")]
OR_ERRS = [("PromoCodeResolver", "NullPointerException", "Cannot invoke Promo.getRules() because promo is null"),
           ("TaxRateClient", "SocketTimeoutException", "Read timed out calling tax-svc"),
           ("InvoiceRenderer", "IllegalStateException", "font cache not initialised"),
           ("ShippingQuoteCache", "ConcurrentModificationException", "zone table changed during refresh")]
IV_MSGS = ["warehouse sync heartbeat ok shards=6", "sku index size=48211 entries", "cache hit ratio 0.91", "gc pause 38ms",
           "replica lag 120ms", "config reload ok"]
IV_ERRS = [("SkuCacheWarmer", "ConnectException", "Connection refused: redis-inv-2:6379"),
           ("BackorderReconciler", "SQLTransientConnectionException", "connection pool exhausted (30/30)"),
           ("CycleCountExporter", "UncheckedIOException", "disk quota exceeded writing /var/spool/export.tmp")]
PA_MSGS = ["webhook evt_{x} received type=charge.succeeded", "webhook evt_{x} verified", "settlement batch closed items=212",
           "fraud-score ok p95=84ms", "3ds challenge completed", "token vault replica lag 90ms", "webhook evt_{x} received type=charge.refunded"]
PA_ERRS = [("WebhookSignatureVerifier", "InvalidSignatureError", "signature mismatch for endpoint /hooks/psp-b"),
           ("FraudScoreClient", "ReadTimeout", "fraud-score read timed out after 2000ms"),
           ("LedgerExporter", "KeyError", "'merchant_ref'")]
DECL = ["card_declined", "insufficient_funds", "expired_card", "do_not_honor", "avs_mismatch"]


def amt(r):
    return "%d.%02d" % (r.randint(9, 640), r.randint(0, 99))


def ids(kind, r, spec):
    return {
        "req": lambda: "r-%08x" % r.getrandbits(32), "trace": lambda: "tr-%012x" % r.getrandbits(48),
        "hold": lambda: "H-%06x" % r.getrandbits(24), "task": lambda: "task-%05d" % r.randint(10000, 99999),
        "pay": lambda: "pi_" + "".join(r.choice(ALN) for _ in range(24))}[kind]() if spec == 1 else {
        "req": lambda: "rq_%010x" % r.getrandbits(40), "trace": lambda: "%032x" % r.getrandbits(128),
        "hold": lambda: "RS-2026-%05d" % r.randint(10000, 99999), "task": lambda: "job-%06d" % r.randint(100000, 999999),
        "pay": lambda: "re_" + "".join(r.choice(ALN) for _ in range(20))}[kind]() if spec == 2 else {
        "req": lambda: str(uuid.UUID(int=r.getrandbits(128), version=4)), "trace": lambda: "%012x" % r.getrandbits(48),
        "hold": lambda: "LS-%07d" % r.randint(1000000, 9999999), "task": lambda: "sweep-%04d" % r.randint(1000, 9999),
        "pay": lambda: "txn_%016x" % r.getrandbits(64)}[kind]()


class B:
    def __init__(s, c):
        s.c, s.r = c, random.Random(c["seed"])
        s.ev = {k: [] for k in SV}
        s.size = dict.fromkeys(SV, 0)
        s.used = set()
        s.h0 = DT.datetime.strptime(c["date"] + " %02d" % c["h0"], "%Y-%m-%d %H")

    def T(s, m, sec=0.0):
        return s.h0 + DT.timedelta(minutes=m, seconds=sec)

    def id(s, kind):
        while True:
            x = ids(kind, s.r, s.c["spec"])
            if x not in s.used:
                s.used.add(x)
                return x

    def mut(s, x):  # near-miss: same shape, one of the last 3 characters changed
        while True:
            i = len(x) - s.r.randint(1, 3)
            pool = ALN if x[i].isalpha() and x[i] not in HEX else (HEX if x[i] in HEX and not x[i].isdigit() else "0123456789")
            y = x[:i] + s.r.choice(pool) + x[i + 1:]
            if y != x and y not in s.used:
                s.used.add(y)
                return y

    def order(s):
        while True:
            o = s.c["ordfmt"] % s.r.randint(s.c["ordlo"], s.c["ordhi"])
            if o != s.c["order"]:
                return o

    def ip(s):
        return "%s.%d.%d.%d" % (s.r.choice(["10", "172", "203", "198", "192"]), s.r.randint(0, 255), s.r.randint(0, 255), s.r.randint(2, 250))

    def stack(s, svc, cls, exc, msg, top=None):
        r, style, pkg = s.r, s.c["stk"][svc], s.c["pkg"][svc]
        meths = ["run", "process", "execute", "handle", "invoke", "dispatch", "call"]
        if style == "java":
            fr = ["\tat com.shop.%s.%s.%s(%s.java:%d)" % (pkg, cls, r.choice(meths), cls, r.randint(30, 900))]
            if top:
                fr = ["\tat com.shop.%s.%s(%s.java:%d)" % (pkg, t, t.split(".")[0], r.randint(30, 400)) for t in top] + fr
            fr += ["\tat java.base/java.util.concurrent.FutureTask.run(FutureTask.java:264)",
                   "\tat java.base/java.util.concurrent.ThreadPoolExecutor.runWorker(ThreadPoolExecutor.java:1136)",
                   "\tat java.base/java.lang.Thread.run(Thread.java:840)"]
            return "\n".join(fr)
        fr = ["Traceback (most recent call last):"]
        for t in (top or []) + [cls + "." + r.choice(meths)]:
            fr += ['  File "/srv/%s/%s.py", line %d, in %s' % (pkg, t.split(".")[0], r.randint(20, 400), t.split(".")[1]), "    self._step()"]
        fr += ['  File "/usr/lib/python3.11/threading.py", line 1038, in _bootstrap_inner', "    self.run()", "%s: %s" % (exc, msg)]
        return "\n".join(fr)

    def add(s, svc, d, kind, stack=None, **f):
        t = s.c["F"][kind].format(ts=ts(d, s.c["ST"][svc]), **f)
        if stack:
            t += "\n" + stack
        s.ev[svc].append((d, len(s.ev[svc]), t))
        s.size[svc] += len(t) + 1

    def err_noise(s, svc, d, tbl, worker=None):
        cls, exc, msg = s.r.choice(tbl)
        th = "http-nio-%d" % s.r.randint(8080, 8089)
        if svc == "or":
            s.add(svc, d, "o_msg", th=th, lvl="ERROR", cls=cls, msg="%s: %s" % (exc, msg), stack=s.stack(svc, cls, exc, msg))
        elif svc == "pa":
            s.add(svc, d, "p_msg", lvl="ERROR", msg="%s: %s: %s" % (cls, exc, msg), stack=s.stack(svc, cls, exc, msg))
        else:
            w, tk = worker or s.r.choice(s.c["ok_w"]), s.id("task")
            s.add(svc, ms(d, -s.r.randint(900, 4000)), "w_start", worker=w, task=tk, cls=cls, extra="batch=%d" % s.r.randint(1, 40))
            s.add(svc, d, "w_err", worker=w, task=tk, cls=cls, exc=exc, msg=msg, n=s.r.choice([0, 0, 0, 1]), stack=s.stack(svc, cls, exc, msg))

    def flow(s, t0, mode, order, worker=None, task=None, pos=1, force=None):
        c, r, force = s.c, s.r, force or {}
        req = force.get("req") or s.id("req")
        trace = force.get("trace") or s.id("trace")
        pi = force.get("pay") or s.id("pay")
        spn = trace[::-1][:16]
        h, th, to = r.choice(HOSTS), "http-nio-%d" % r.randint(8080, 8089), c["to"]
        path = c["path"].format(order=order)
        s.add("gw", t0, "g_start", h=h, req=req, method=c["method"], path=path, order=order, ip=s.ip(), ua=r.choice(UAS))
        s.add("gw", ms(t0, r.randint(2, 6)), "g_up", h=h, req=req, trace=trace, spn=spn, to=to)
        s.add("or", ms(t0, r.randint(9, 14)), "o_begin", th=th, trace=trace, n=r.randint(1, 5))
        out = dict(req=req, trace=trace, pi=pi, status=c["status"] if mode != "ok" else 200)
        if mode == "rej":
            s.add("or", ms(t0, 16), "o_dup", th=th, trace=trace)
            s.add("gw", ms(t0, 21), "g_done", h=h, lvl="WARN", req=req, status=409, dur=r.randint(14, 30), b=96)
            return out
        s.add("pa", ms(t0, r.randint(18, 40)), "p_create", pi=pi, order=order, amt=amt(r))
        t1 = ms(t0, r.randint(45, 70))
        h1, sku = s.id("hold"), "SKU-%05d" % r.randint(10000, 60000)
        w1, wt = r.choice(c["ok_w"]), r.randint(8, 60)
        s.add("or", t1, "o_req", th=th, trace=trace, sku=sku, q=r.randint(1, 4), hold=h1)
        s.add("iv", ms(t1, 3), "i_ok", hold=h1, sku=sku, q=2, worker=w1, ms=wt)
        s.add("or", ms(t1, wt + 7), "o_ok", th=th, trace=trace, hold=h1, ms=wt + 4)
        if mode == "db":
            td = ms(t0, r.randint(300, 1200))
            s.add("or", td, "o_dbfail", th=th, trace=trace, stack=s.stack("or", "OrderRepository", "SQLTransientConnectionException", "pool", ["OrderRepository.getConnection"]))
            s.add("iv", ms(td, 40), "i_rel", hold=h1)
            s.add("gw", ms(td, 52), "g_done", h=h, lvl="ERROR", req=req, status=c["status"], dur=int((ms(td, 52) - t0).total_seconds() * 1000), b=r.randint(120, 180))
            s.add("pa", ms(td, 60), "p_cancel", pi=pi)
            return out
        t2 = ms(t1, r.randint(60, 120))
        h2, sku2 = force.get("hold") or s.id("hold"), "SKU-%05d" % r.randint(10000, 60000)
        s.add("or", t2, "o_req", th=th, trace=trace, sku=sku2, q=r.randint(1, 3), hold=h2)
        out["hold"] = h2
        if mode == "ok":
            w2, wt2 = r.choice(c["ok_w"]), r.randint(8, 70)
            s.add("iv", ms(t2, 3), "i_ok", hold=h2, sku=sku2, q=1, worker=w2, ms=wt2)
            s.add("or", ms(t2, wt2 + 7), "o_ok", th=th, trace=trace, hold=h2, ms=wt2 + 4)
            te = ms(t2, wt2 + r.randint(30, 150))
            s.add("or", te, "o_done", th=th, trace=trace, amt=amt(r))
            s.add("gw", ms(te, 4), "g_done", h=h, lvl="INFO", req=req, status=200, dur=int((ms(te, 4) - t0).total_seconds() * 1000), b=r.randint(300, 900))
            s.add("pa", ms(te, 12), "p_ok", pi=pi)
            s.add("iv", ms(te, 30), "i_rel", hold=h1)
            s.add("iv", ms(te, 33), "i_rel", hold=h2)
            return out
        s.add("iv", ms(t2, 3), "i_queued", hold=h2, worker=worker, task=task, pos=pos)
        ta = ms(t0, to + r.randint(4, 9))
        out["w"] = int((ta - t2).total_seconds() * 1000)
        out["dur"] = to + r.randint(1, 3)
        s.add("gw", ms(t0, out["dur"]), "g_done", h=h, lvl="ERROR", req=req, status=c["status"], dur=out["dur"], b=r.randint(120, 180))
        s.add("or", ta, "o_abort", th=th, trace=trace, hold=h2, w=out["w"])
        s.add("pa", ms(ta, 11), "p_cancel", pi=pi)
        return out

    def group(s, worker, task, cls, exc, tstart, twarn, terr, t0s, orders, top, force=None):
        c = s.c
        fl = [s.flow(t0, "stuck", o, worker, task, i + 1, (force or {}).get(o)) for i, (t0, o) in enumerate(sorted(zip(t0s, orders)))]
        s.add("iv", tstart, "w_start", worker=worker, task=task, cls=cls, extra=c["xtra"])
        for k, pct in enumerate((18, 37, 52)):
            tp = ms(tstart, 7000 + 8300 * k + s.r.randint(0, 900))
            if tp < twarn:
                s.add("iv", tp, "w_info", worker=worker, task=task, cls=cls, msg="progress %d%% segments_done=%d" % (pct, pct // 6))
        s.add("iv", twarn, "w_warn", worker=worker, task=task, cls=cls, msg=c["warn1"])
        s.add("iv", ms(twarn, 9400 + s.r.randint(0, 300)), "w_warn", worker=worker, task=task, cls=cls, msg=c["warn2"])
        s.add("iv", terr, "w_err", worker=worker, task=task, cls=cls, exc=exc, msg=c["errmsg"], n=len(t0s), stack=s.stack("iv", cls, exc, c["errmsg"], top))
        for i, f in enumerate(fl):
            s.add("iv", ms(terr, 25 + 6 * i), "i_exp", hold=f["hold"], worker=worker, task=task)
        return fl

    def routine(s, d, worker=None):
        c, r = s.c, s.r
        w, tk, cls = worker or r.choice(c["all_w"]), s.id("task"), r.choice(c["rclasses"])
        s.add("iv", d, "w_start", worker=w, task=tk, cls=cls, extra="batch=%d" % r.randint(1, 60))
        dur = r.randint(300, 4000)
        if r.random() < 0.25:
            s.add("iv", ms(d, dur // 2), "w_warn", worker=w, task=tk, cls=cls, msg=r.choice(["slow scan %dms" % r.randint(1200, 2600), "retrying after transient error (attempt 2/5)", "lag behind schedule by %ds" % r.randint(2, 9)]))
        s.add("iv", ms(d, dur), "w_ok", worker=w, task=tk, cls=cls, ms=dur)

    def solo_gw(s, d):
        c, r = s.c, s.r
        h, req, o = r.choice(HOSTS), s.id("req"), s.order()
        method, p = r.choice(c["gpaths"])
        p = p.format(order=o, sku="SKU-%05d" % r.randint(10000, 60000))
        x = r.random()
        st, dur = (200, r.randint(4, 220)) if x < .84 else r.choice([(404, 9), (401, 6), (429, 3), (400, 11), (500, r.randint(30, 400)), (502, r.randint(1500, 1700)), (503, 40)])
        s.add("gw", d, "g_start", h=h, req=req, method=method, path=p, order=o if "{order}" in p or o in p else "-", ip=s.ip(), ua=r.choice(UAS))
        s.add("gw", ms(d, dur), "g_done", h=h, lvl="INFO" if st < 400 else ("WARN" if st < 500 else "ERROR"), req=req, status=st, dur=dur, b=r.randint(80, 4000))

    def pad(s, svc, d):
        r = s.r
        if svc == "gw":
            if r.random() < .6:
                s.solo_gw(d)
            else:
                s.add("gw", d, "g_msg", h=r.choice(HOSTS), lvl=r.choice(["INFO", "WARN", "WARN", "ERROR"]), msg=r.choice(GW_MSGS))
        elif svc == "or":
            x = r.random()
            if x < .09:
                s.err_noise("or", d, OR_ERRS)
            else:
                cls, lvl, msg = r.choice(OR_MSGS)
                s.add("or", d, "o_msg", th="sched-%d" % r.randint(1, 4), lvl=lvl, cls=cls, msg=msg)
        elif svc == "iv":
            x = r.random()
            if x < .08:
                s.err_noise("iv", d, IV_ERRS)
            elif x < .5:
                s.routine(d)
            else:
                s.add("iv", d, "i_rel", hold=s.id("hold"))
        else:
            x = r.random()
            if x < .09:
                s.err_noise("pa", d, PA_ERRS)
            elif x < .3:
                o = s.order()
                s.add("pa", d, "p_decl", pi=s.id("pay"), order=o, reason=r.choice(DECL))
            else:
                s.add("pa", d, "p_msg", lvl=r.choice(["INFO", "INFO", "INFO", "DEBUG"]), msg=r.choice(PA_MSGS).replace("{x}", "%014x" % r.getrandbits(56)))

    def rt(s, lo=0, hi=60):  # random time in the hour, away from the incident minutes
        while True:
            m = s.r.uniform(lo, hi)
            if abs(m - s.c["m"]) > 2.5:
                return s.T(m)

    def build(s):
        c, r = s.c, s.r
        m, ORD = c["m"], c["order"]
        T = s.T
        ours_t0 = T(m, 1.806)
        main_w, main_cls, main_exc = c["bad_w"], c["rcls"], c["rexc"]
        # --- main stuck group (ours + 5 holds blocked behind the same task) ---
        near = c["near"]
        orders = [ORD, near[0]] + [s.order() for _ in range(4)]
        t0s = [ours_t0] + sorted(T(m, -17 + 3.1 * i + r.uniform(0, 1.2)) for i in range(5))
        ours_task = s.id("task")
        tstart, twarn, terr = T(m - 1, 19.2), T(m - 1, 44.371), T(m, 13.482)
        force = {ORD: {"hold": s.id("hold")}}
        fl = s.group(main_w, ours_task, main_cls, main_exc, tstart, twarn, terr, t0s, orders, c["top"], force)
        ours = [f for f in fl if f["req"] and f.get("hold") == force[ORD]["hold"]][0]
        # --- same-minute decoys: sweeper group on another worker, db-pool failures, a fast-failing healthy worker ---
        dw, dcls, dexc, dtask = c["dec_w"], c["dcls"], c["dexc"], s.id("task")
        s.group(dw, dtask, dcls, dexc, T(m - 1, 30.4), T(m - 1, 52.9), T(m, 6.115), [T(m, -9.4), T(m, -3.3), T(m, 0.9)],
                [near[1], s.order(), s.order()], c["dtop"])
        # --- earlier compactor failure on the other worker (1 blocked), earlier clean run on the bad worker ---
        s.group(dw, s.id("task"), main_cls, main_exc, T(m - 21, 40.2), T(m - 20, 31.8), T(m - 20, 52.5), [T(m - 20, 40.0)], [s.order()], c["top"])
        et, ew = s.id("task"), T(m - 27, 11.7)
        s.add("iv", ew, "w_start", worker=main_w, task=et, cls=main_cls, extra=c["xtra"])
        s.add("iv", ms(ew, 9000), "w_warn", worker=main_w, task=et, cls=main_cls, msg=c["warn1"])
        s.add("iv", ms(ew, 14100), "w_ok", worker=main_w, task=et, cls=main_cls, ms=14100)
        st_ = s.id("task")  # concurrent healthy task on the bad worker during the incident
        s.add("iv", T(m - 1, 36.0), "w_start", worker=main_w, task=st_, cls=dcls, extra="batch=12")
        for k in range(3):
            s.add("iv", T(m - 1, 41 + 6 * k + r.random()), "w_info", worker=main_w, task=st_, cls=dcls, msg="progress %d%% scanned=%d" % (30 * (k + 1), 400 * (k + 1)))
        s.add("iv", T(m, 3.5), "w_ok", worker=main_w, task=st_, cls=dcls, ms=87500)
        for t_ in (T(m - 0.5, 11.1), T(m, 4.4), T(m, 12.7)):
            s.flow(t_, "db", s.order())
        s.err_noise("iv", T(m, 8.2), IV_ERRS, c["ok_w"][0])
        s.err_noise("iv", T(m - 1, 50.7), IV_ERRS[1:], c["ok_w"][1])
        s.err_noise("or", T(m, 5.3), OR_ERRS)
        s.err_noise("or", T(m - 1, 58.9), OR_ERRS[1:])
        s.err_noise("pa", T(m, 6.6), PA_ERRS)
        s.err_noise("pa", T(m, 11.9), PA_ERRS[1:])
        # --- our order's neighbours in the gateway: earlier GET, retry (409), later GET; near-miss flows ---
        for dt_, mt, p in ((-44, "GET", c["getpath"]), (71, "GET", c["getpath"])):
            h, rq = r.choice(HOSTS), s.id("req")
            s.add("gw", ms(ours_t0, dt_ * 1000), "g_start", h=h, req=rq, method=mt, path=p.format(order=ORD), order=ORD, ip=s.ip(), ua=r.choice(UAS))
            s.add("gw", ms(ours_t0, dt_ * 1000 + 31), "g_done", h=h, lvl="INFO", req=rq, status=200, dur=31, b=412)
        s.flow(ms(ours_t0, 23400), "rej", ORD)
        s.flow(T(m - 9.2), "ok", near[0], force={"req": s.mut(ours["req"]), "trace": s.mut(ours["trace"]), "pay": s.mut(ours["pi"])})
        s.flow(T(m + 6.1), "ok", near[1], force={"hold": s.mut(ours["hold"])})
        s.flow(T(m + 11.7), "ok", c["near"][2])
        # --- bulk: ordinary traffic until one service is ~80% full, then pad every service to its target ---
        tgt = {k: r.randint(72000, 90000) for k in SV}
        while max(s.size[k] / float(tgt[k]) for k in SV) < 0.8:
            s.flow(s.rt(), "ok", s.order())
        for k in SV:
            while s.size[k] < tgt[k]:
                s.pad(k, s.rt())
                out = {}
        for k in SV:
            out[k] = "\n".join(t for _, _, t in sorted(s.ev[k], key=lambda e: (e[0], e[1]))) + "\n"
        inv_first_warn = ts(twarn, c["ST"]["iv"])
        ans = [ours["req"], ours["trace"], ours["hold"], main_w, main_cls, ours["pi"], c["status"], c["to"], ours["w"], main_exc,
               {"warn": inv_first_warn, "start": ts(tstart, c["ST"]["iv"])}[c["k11"]], len(t0s)]
        return out, ans


def cfg(n):
    base = dict(date="2026-09-17", h0=14, m=32, order="ORD-77341", ordfmt="ORD-%05d", ordlo=70000, ordhi=79999, method="POST", path="/api/checkout",
                getpath="/api/orders/{order}", status=502, to=8000, spec=1, F=F1, near=["ORD-77314", "ORD-77431", "ORD-67341"],
                ST=dict(gw="iso", **{"or": "java", "iv": "tz", "pa": "plain"}), stk=dict(gw="java", **{"or": "java", "iv": "java", "pa": "py"}),
                pkg={"or": "orders", "iv": "inventory.worker", "pa": "payments", "gw": "gateway"},
                bad_w="inv-w3", dec_w="inv-w5", ok_w=["inv-w1", "inv-w2", "inv-w4", "inv-w6"], rcls="StockLedgerCompactor", rexc="LedgerLockTimeoutException",
                dcls="ReservationExpirySweeper", dexc="OptimisticLockException", rclasses=["SkuCacheWarmer", "BackorderReconciler", "ReservationExpirySweeper", "StockLedgerCompactor"],
                xtra="segment=ledger-2026-09-17 mode=full", warn1="waiting for ledger segment lock held by txn-4471 (waited 5000ms)",
                warn2="still waiting for ledger segment lock (waited 14500ms)", errmsg="could not acquire segment lock ledger-2026-09-17 within 30000ms",
                top=["LedgerLockManager.acquire", "StockLedgerCompactor.compactSegment"], dtop=["ReservationTable.update", "ReservationExpirySweeper.sweepBatch"],
                gpaths=[("GET", "/api/products/{sku}"), ("GET", "/api/cart"), ("POST", "/api/login"), ("GET", "/api/orders/{order}"), ("POST", "/api/cart/items"),
                        ("GET", "/api/recommendations"), ("GET", "/api/products/{sku}"), ("GET", "/healthz")], k11="warn")
    if n == 2:
        base.update(date="2026-10-03", h0=9, m=47, order="ORD-20518", ordfmt="ORD-%05d", ordlo=19800, ordhi=21200, path="/api/refunds", getpath="/api/orders/{order}/refunds",
                    status=504, to=12000, spec=2, F=F2, near=["ORD-20581", "ORD-20158", "ORD-20519"], seed=202,
                    ST=dict(gw="iso", **{"or": "iso", "iv": "plain", "pa": "java"}), stk=dict(gw="java", **{"or": "py", "iv": "java", "pa": "java"}),
                    bad_w="stockd-b4", dec_w="stockd-b7", ok_w=["stockd-b1", "stockd-b2", "stockd-b3", "stockd-b5", "stockd-b6"],
                    rcls="ShelfIndexRebuilder", rexc="IndexSegmentLockedException", dcls="BinLocationRecalculator", dexc="DeadlockLoserException",
                    rclasses=["CycleCountScheduler", "BinLocationRecalculator", "ShelfIndexRebuilder", "ReturnsDockSync"],
                    xtra="index=shelf_idx_v7 mode=rebuild", warn1="segment lock shelf_idx_v7/3 held by writer 1182, waiting (5000ms)",
                    warn2="segment lock shelf_idx_v7/3 still held, waiting (15000ms)", errmsg="rebuild aborted, segment lock shelf_idx_v7/3 not released within 30000ms",
                    top=["SegmentLockTable.await", "ShelfIndexRebuilder.rebuildSegment"], dtop=["BinTable.update", "BinLocationRecalculator.recalc"],
                    gpaths=[("GET", "/api/products/{sku}"), ("GET", "/api/orders/{order}"), ("POST", "/api/login"), ("GET", "/api/returns/labels"),
                            ("GET", "/api/orders/{order}/refunds"), ("GET", "/api/account"), ("GET", "/healthz")], k11="warn")
    if n == 3:
        base.update(date="2026-10-21", h0=21, m=38, order="ORD-61209", ordfmt="ORD-%05d", ordlo=60500, ordhi=62000, method="PATCH", path="/api/orders/{order}/address",
                    getpath="/api/orders/{order}", status=503, to=5000, spec=3, F=F3, near=["ORD-61290", "ORD-16209", "ORD-61207"], seed=303, k11="start",
                    ST=dict(gw="plain", **{"or": "tz", "iv": "java", "pa": "iso"}), stk=dict(gw="java", **{"or": "py", "iv": "java", "pa": "py"}),
                    bad_w="wh-agent-02", dec_w="wh-agent-05", ok_w=["wh-agent-01", "wh-agent-03", "wh-agent-04", "wh-agent-06"],
                    rcls="SlotRebalancer", rexc="SlotTableLockTimeoutException", dcls="PalletAssignmentSync", dexc="StaleVersionException",
                    rclasses=["DockCalendarSync", "PalletAssignmentSync", "SlotRebalancer", "CarrierManifestPusher"],
                    xtra="zone=C table=slot_assign mode=rebalance", warn1="slot_assign table lock contended by writer w-9120 (waited 4000ms)",
                    warn2="slot_assign table lock still contended (waited 12000ms)", errmsg="rebalance aborted: table lock slot_assign not acquired within 25000ms",
                    top=["SlotTableLock.acquire", "SlotRebalancer.rebalanceZone"], dtop=["PalletTable.save", "PalletAssignmentSync.syncBatch"],
                    gpaths=[("GET", "/api/orders/{order}"), ("GET", "/api/products/{sku}"), ("POST", "/api/login"), ("GET", "/api/addresses"),
                            ("PUT", "/api/addresses/default"), ("GET", "/api/orders"), ("GET", "/healthz")])
    base["all_w"] = [base["bad_w"], base["dec_w"]] + base["ok_w"]
    return base


# ---- prompts ----
SVC_DESC = ["`logs/gateway.log` – API gateway", "`logs/orders.log` – orders service", "`logs/inventory.log` – inventory service", "`logs/payments.log` – payments service"]
TAIL = """- `logs/gateway.log` – API gateway
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
"""
KEYS = {
    1: [("gateway_request_id", "the gateway's request ID (`req=`) of the failed POST /api/checkout for ORD-77341."),
        ("upstream_trace_id", "the trace ID the gateway recorded for the upstream call of that request."),
        ("hold_id", "the inventory hold that the orders service was waiting on for that trace."),
        ("worker", "the inventory worker that hold was queued on."),
        ("root_cause_component", "the software component that caused the failure (a class name, not a service or worker\n   name), named exactly as it is spelled in the logs."),
        ("payment_intent_id", "the payment intent created for ORD-77341."),
        ("gateway_status", "the HTTP status code the gateway logged when it answered that request (integer)."),
        ("upstream_timeout_ms", "the timeout in milliseconds the gateway recorded for the upstream call of that request (integer)."),
        ("hold_wait_ms", "how many milliseconds the orders service reports it had waited for that hold when it gave up (integer)."),
        ("root_cause_exception", "the exception class that terminated the task of the root-cause component which blocked the hold, as logged."),
        ("first_failure_timestamp", "the timestamp, exactly as written in `logs/inventory.log`, of the first WARN or ERROR line logged by\n    the task that blocked the hold."),
        ("blocked_holds_count", "the number of holds that the blocking task's ERROR line reports as blocked (integer).")],
    2: [("gateway_request_id", "the gateway's request ID (`request_id`) of the failed POST /api/refunds for ORD-20518."),
        ("upstream_trace_id", "the 32-hex trace ID inside the `traceparent` the gateway sent upstream for that request."),
        ("restock_ticket_id", "the restock ticket the orders service was waiting on for that trace."),
        ("worker", "the stock-daemon worker that ticket was parked on."),
        ("root_cause_component", "the software component that caused the failure (a class name, not a service or worker name),\n   named exactly as it is spelled in the logs."),
        ("refund_id", "the payment refund created for ORD-20518."),
        ("http_status", "the HTTP status code the gateway logged for that request (integer)."),
        ("gateway_timeout_ms", "the upstream timeout in milliseconds the gateway logged for that request (integer)."),
        ("ticket_wait_ms", "how many milliseconds the orders service reports it had waited for the ticket when the caller went away (integer)."),
        ("root_cause_error", "the exception class that ended the task of the root-cause component which the ticket was waiting for."),
        ("first_warning_at", "the timestamp, exactly as written in `logs/inventory.log`, of the first WARN or ERROR line of that task."),
        ("stalled_tickets", "the number of stalled tickets the task's ERROR line reports (integer).")],
    3: [("gateway_request_id", "the gateway's request ID (`rid=`) of the failed PATCH /api/orders/ORD-61209/address."),
        ("upstream_trace_id", "the `x-trace-id` the gateway recorded for the upstream call of that request."),
        ("lease_id", "the warehouse slot lease the orders service was waiting on for that trace."),
        ("worker", "the warehouse agent that lease was queued on."),
        ("root_cause_component", "the software component that caused the failure (a class name, not a service or agent name),\n   named exactly as it is spelled in the logs."),
        ("payment_txn_id", "the payment authorization transaction created for ORD-61209."),
        ("status_code", "the HTTP status code the gateway logged when it answered that request (integer)."),
        ("upstream_timeout_ms", "the upstream timeout in milliseconds the gateway logged for that request (integer)."),
        ("lease_wait_ms", "how many milliseconds the orders service reports it had waited for the lease when the caller disconnected (integer)."),
        ("exception_class", "the exception class that made the blocking task fail, as logged."),
        ("blocking_task_started_at", "the timestamp, exactly as written in `logs/inventory.log`, of the START line of the task that\n    blocked the lease."),
        ("queued_leases", "the number of queued leases the blocking task's FAILED line reports (integer).")],
}
INTRO = {2: ("refund for order ORD-20518", "2026-10-03 09:47", "a customer's refund for order `ORD-20518`", "HTTP 504 Gateway Timeout  (POST /api/refunds, order ORD-20518)"),
         3: ("address change for order ORD-61209", "2026-10-21 21:38", "a customer's delivery-address change for order `ORD-61209`", "HTTP 503 Service Unavailable  (PATCH /api/orders/ORD-61209/address)")}


def prompt(n):
    if n == 1:
        head = HEAD1.rstrip("\n")
    else:
        t, when, what, http = INTRO[n]
        head = ("**CONSTRAINT: Your final deliverable is a file named `REPORT.json` in the current directory, containing one JSON\n"
                "object with exactly the 12 keys listed at the end of this task. Write it before you finish.**\n\n"
                "# Incident: %s\n\nAt %s UTC %s failed. The client received:\n\n```\n%s\n```\n\n"
                "Logs from the four services involved are in `logs/`:\n\n" % (t, when, what, http) + TAIL.rstrip("\n") + "\n\n")
        head += "\n".join("%d. `%s` — %s" % (i, k, d) for i, (k, d) in enumerate(KEYS[n][:6], 1))
    return head + "\n" + "\n".join("%d. `%s` — %s" % (i, k, d) for i, (k, d) in enumerate(KEYS[n][6:], 7)) + "\n"


NAMES = {1: "dbg_01_checkout", 2: "dbg_02_refund", 3: "dbg_03_address_change"}


def write(n):
    c = cfg(n)
    if "seed" not in c:
        c["seed"] = 101
    logs, ans = B(c).build()
    d = os.path.join(CASES, NAMES[n])
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(os.path.join(d, "setup", "logs"))
    p = prompt(n)
    open(os.path.join(d, "prompt.txt"), "w", encoding="utf-8").write(p)
    open(os.path.join(d, "setup", "TASK.md"), "w", encoding="utf-8").write(p)
    for k, f in FILES.items():
        open(os.path.join(d, "setup", "logs", f), "w", encoding="utf-8").write(logs[k])
    eq = {k: v for (k, _), v in zip(KEYS[n], ans)}
    json.dump({"status": "any", "timeout": 300, "allow_new_files": True, "json_file": {"path": "REPORT.json", "equals": eq, "exact_keys": True}},
              open(os.path.join(d, "expect.json"), "w", encoding="utf-8"), indent=2)
    print(NAMES[n], {f: len(v) for f, v in zip(FILES.values(), logs.values())})


# ---- independent validation: follow the chain with grep-like steps ----
def grab(line, pat):
    m = re.search(pat, line)
    return m.group(1) if m else None


def check(n):
    d = os.path.join(CASES, NAMES[n])
    exp = json.load(open(os.path.join(d, "expect.json")))["json_file"]["equals"]
    L = {k: open(os.path.join(d, "setup", "logs", f), encoding="utf-8").read().splitlines() for k, f in FILES.items()}
    sizes = {k: sum(len(x) + 1 for x in v) for k, v in L.items()}
    assert all(60000 <= v <= 100000 for v in sizes.values()), sizes
    keys = [k for k, _ in KEYS[n]]
    order = {1: "ORD-77341", 2: "ORD-20518", 3: "ORD-61209"}[n]
    post = {1: "POST /api/checkout", 2: "POST /api/refunds", 3: "PATCH /api/orders/ORD-61209/address"}[n]
    # hop 1: gateway start line for the order that is a POST -> req ; the one whose done status is a 5xx
    cands = [grab(l, r'(?:req=|request_id":"|rid=)([\w-]+)') for l in L["gw"] if order in l and post.split()[1] in l and (post.split()[0] in l)]
    fails = []
    for rq in cands:
        done = [l for l in L["gw"] if rq in l and ("status" in l or "<-" in l)][0]
        st = int(grab(done, r'(?:status=|"status":|<- )(\d+)'))
        if st >= 500:
            fails.append((rq, st, done))
    assert len(fails) == 1, fails
    rq, st, done = fails[0]
    up = [l for l in L["gw"] if rq in l and "timeout" in l]
    assert len(up) == 1
    tr = grab(up[0], r'(?:trace=|traceparent":"00-|x-trace-id=)([0-9a-zA-Z-]+?)(?:-[0-9a-f]{16}-01|\s|"|$)')
    to = int(grab(up[0], r'timeout(?:_ms)?(?:=|":)(\d+)'))
    ol = [l for l in L["or"] if tr in l]
    pend = [l for l in ol if "pending" in l or "not acquired" in l]
    assert len(pend) == 1
    hold = grab(pend[0], r'(?:hold=|ticket=|lease )(\S+)')
    wait = int(grab(pend[0], r'(?:waited=|waited_ms=|after )(\d+)'))
    q = [l for l in L["iv"] if hold in l and ("queued" in l or "parked" in l or "QUEUED" in l)]
    assert len(q) == 1
    worker = grab(q[0], r'(?:worker=|-> )([\w-]+)')
    task = grab(q[0], r'\b((?:task|job|sweep)-\d+)')
    tl = [l for l in L["iv"] if task in l]
    cls = grab(tl[0], r'class=(\w+)') or grab(tl[0], task + r'[ :]+(\w+)')
    errl = [l for l in tl if "ERROR" in l or "FAILED" in l]
    assert len(errl) == 1, (n, errl)
    exc = grab(errl[0], r'(?:failed: |FAILED |ERROR task=\S+ \w+: )(\w+)')
    nb = int(grab(errl[0], r'(?:blocked_holds|stalled_tickets|queued_leases)=(\d+)'))
    # a hold queued on that task per queue lines == the count
    nq = len([l for l in L["iv"] if task in l and ("queued" in l.lower() or "parked" in l) and "task" in l or (task in l and "QUEUED" in l)])
    assert nq == nb, (nq, nb)
    first = [l for l in tl if " WARN" in l or "ERROR" in l or "FAILED" in l][0]
    start = tl[0]
    tsx = lambda l: grab(l, r'(\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d[.,]\d{3}(?:Z|\+00:00)?)')
    pl = [l for l in L["pa"] if order in l and ("create" in l or "adjust" in l)]
    assert len(pl) == 1
    pi = grab(pl[0], r'(?:id=|txn=)(\S+)')
    got = [rq, tr, hold, worker, cls, pi, st, to, wait, exc, tsx(start if n == 3 else first), nb]
    for k, g in zip(keys, got):
        assert exp[k] == g and type(exp[k]) == type(g), (k, exp[k], g)
        assert any(str(g) in l for k2 in L.values() for l in k2)
    assert [type(exp[k]) for k in keys[6:9] + keys[11:]] == [int] * 4
    print("OK", NAMES[n], sizes)


if __name__ == "__main__":
    for i in (1, 2, 3):
        check(i) if "--check" in sys.argv else write(i)
