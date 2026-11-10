---
id: KB-005
title: "API Request Timeouts and 504 Gateway Timeout Diagnosis"
category: api-reliability
component: "Request lifecycle (ASGI server, reverse proxy, async handlers, and downstream calls)"
severity: high
tags:
  - timeout
  - 504-gateway-timeout
  - event-loop-blocking
  - httpx
  - slow-query
  - latency
  - async
source_type: synthetic
applies_to: "FastAPI >= 0.100, Uvicorn >= 0.27, HTTPX >= 0.25, Python >= 3.10"
last_updated: 2026-11-10
related_documents:
  - KB-008
  - KB-006
  - KB-001
---

# API Request Timeouts and 504 Gateway Timeout Diagnosis

## 1. Problem Overview

A timeout is not one event; it is whichever deadline expires first. A single request usually
passes several layers, each with its own limit:

| Layer | Typical symptom when it expires |
| --- | --- |
| Browser/client deadline | Client aborts; server may still be working |
| CDN / load balancer | `504` (or `502` if the connection drops) |
| Reverse proxy (nginx, ingress) | `504 Gateway Timeout` |
| ASGI server keep-alive / shutdown | Connection closed mid-request |
| Application-level handler deadline | Custom `503`/`504` with a JSON body |
| Downstream HTTP call (`httpx` timeout) | `httpx.ReadTimeout`, often mapped to `500` or `504` |
| Database statement timeout | `canceling statement due to statement timeout` |
| Connection pool checkout | `QueuePool limit ... reached` (see KB-008) |

Two facts drive most investigations:

- **The layer that reports the timeout is rarely the layer that caused it.** A `504` from the
  proxy says only that the upstream did not respond in time; it says nothing about which query
  or API call consumed the budget.
- **A timeout is a symptom of retained work, not of "the server being slow" in the abstract.**
  Either one unit of work takes too long (slow query, missing index, large payload, CPU-bound
  loop) or too many units compete for a fixed resource (event loop blocked, thread pool
  saturated, pool exhausted).

Async adds its own trap: a single blocking call inside `async def` blocks the entire event loop,
so one slow request delays every concurrent request on that worker. That produces timeouts
across unrelated endpoints, which looks like an infrastructure problem and is actually a code
defect.

## 2. Symptoms and Typical Error Messages

**Proxy-generated**

```html
<html>
<head><title>504 Gateway Timeout</title></head>
<body><center><h1>504 Gateway Timeout</h1></center></body>
</html>
```

The HTML body and absence of your JSON error shape are the clue: the app never replied.

**Server-side**

```text
httpx.ReadTimeout: The read operation timed out
httpx.ConnectTimeout: timed out
asyncio.TimeoutError
TimeoutError: (110, 'Connection timed out')
```

```text
sqlalchemy.exc.OperationalError: (psycopg2.errors.QueryCanceled)
canceling statement due to statement timeout
```

```text
INFO:     ('203.0.113.10', 51234) - "GET /orders/search?q=x HTTP/1.1" 200 OK
```
with a duration far above the client's deadline, i.e. the work finished after the client left.

**Behavioural patterns**

- One endpoint times out; others are unaffected → work in that handler.
- All endpoints intermittently time out → event loop blocked, pool exhausted, or CPU starvation.
- Timeouts begin at a specific traffic level → capacity, not a regression in logic.
- Timeouts begin after a deploy → new synchronous call, heavier response, or a regression in a
  query plan.
- `200` in access logs at a time when clients report `504` → the response exceeded the proxy
  deadline before headers were flushed.
- Timeouts only on cold start → connection establishment or model loading counted against the
  request budget.

**Client-visible confusion**

- `502` instead of `504` when the proxy gives up by closing the upstream connection.
- A retried request succeeds because a cache or connection was warm, masking the real cause.
- Duplicate side effects when a non-idempotent write times out and the client retries.

## 3. Possible Root Causes

**Slow single unit of work**

- Missing index or a query plan regression (statistics stale, plan flip after a data growth
  threshold).
- N+1 queries: the endpoint issues one query per row inside a loop.
- Unbounded `SELECT` with no pagination, serialising tens of thousands of rows.
- Large payload assembly (file export, PDF generation) inside the request instead of a job.
- CPU-bound work (hashing, image processing, big JSON transforms) inside `async def`.
- Cold start: driver connection, TLS handshake, DNS, and template/model loading on first request.

**Blocked or starved execution**

- Blocking libraries (`requests`, `psycopg2` sync, `time.sleep`, file I/O) called from `async def`,
  stalling the event loop for every concurrent request on that worker.
- CPU-bound code in the event loop with no `run_in_threadpool` or process offload.
- Sync `def` handlers exhausting the thread pool, so requests queue before their handler runs.
- GIL contention from many threads doing CPU work.

**Missing or mis-set deadlines**

- No timeout on downstream HTTP clients; a hanging dependency holds the request until the proxy
  gives up.
- No `statement_timeout`/`lock_timeout` on the database session.
- Client timeout longer than the proxy timeout, so the client waits on a request that is already
  dead — or shorter, so the server keeps working for nobody.
- Retries without a total budget: three 10-second attempts inside a 15-second proxy deadline.

**Resource limits**

- Connection pool checkout wait counted inside the request budget (see KB-008).
- File descriptor or ephemeral port exhaustion under high connection churn.
- Memory pressure causing swapping, which turns 50 ms operations into seconds.

## 4. Step-by-Step Investigation

**Diagnostic only.** Establish where the budget is spent before changing any timeout value.

1. **Measure the breakdown from outside**, separating DNS, connect, TLS, and response:

   ```bash
   curl -o /dev/null -sS \
     -w 'dns=%{time_namelookup} connect=%{time_connect} tls=%{time_appconnect} first_byte=%{time_starttransfer} total=%{time_total}\n' \
     http://127.0.0.1:8000/orders/search?q=widget
   ```

   A large `first_byte` with small connect times points inside the application; large `connect`
   points at the network or a saturated accept queue.

2. **Hit the app directly, bypassing the proxy.** If direct is fast and proxied is `504`, the
   proxy's deadline (or its upstream resolution) is the issue, and app tuning is misdirected.

3. **Instrument request duration server-side.** Log elapsed time with the route and status:

   ```python
   import logging
   import time

   from fastapi import FastAPI, Request

   logger = logging.getLogger("opspilot.timing")
   app = FastAPI()


   @app.middleware("http")
   async def timing_middleware(request: Request, call_next):
       started = time.perf_counter()
       response = await call_next(request)
       elapsed_ms = (time.perf_counter() - started) * 1000
       if elapsed_ms > 500:
           logger.warning("slow_request path=%s status=%s ms=%.1f",
                          request.url.path, response.status_code, elapsed_ms)
       return response
   ```

   Use `perf_counter`, not `time.time`, for durations.

4. **Identify the slow span.** With no tracer, time each stage explicitly (query, downstream
   call, serialisation) for the failing route. With a tracer, read the span tree: a dominant
   database span and a dominant external span call for different fixes.

5. **Detect event-loop blocking.** Correlate `async def` handlers with blocking calls, and watch
   for a telltale pattern: *all* endpoints slow at the same moments.

   ```bash
   # Sampling profiler (install only in a diagnostic image; do not add to requirements)
   py-spy top --pid "$(pgrep -f 'uvicorn' | head -n1)"
   ```

   > **Tooling note:** `py-spy` reads process memory and may need elevated permissions. Prefer
   > it on a staging replica rather than production, and never add it to runtime dependencies.

6. **Check the database's view.** Confirm whether statements are still running after the client
   gave up, and whether they are waiting on locks:

   ```sql
   select pid, now() - query_start as duration, wait_event_type, state,
          left(query, 100) as query
   from pg_stat_activity
   where datname = current_database() and state = 'active'
   order by duration desc limit 10;
   ```

   Long `Lock` waits mean a blocking transaction elsewhere, not a slow query.

7. **Verify pool and thread-pool saturation at the moment of failure** (see KB-008). A request
   that spends its budget waiting for a connection is a capacity problem, not a latency one.

8. **Reproduce with a deadline** to confirm which layer aborts first:

   ```bash
   curl -m 5 -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/orders/search?q=widget
   # exit code 28 = client deadline hit before a response
   ```

## 5. Safe Troubleshooting Recommendations

Remediation. Prefer fixing retained work over extending deadlines; raising a timeout without
evidence only moves the failure later and consumes more capacity.

- **Set explicit deadlines at every boundary.** Never rely on a default:

  ```python
  import httpx

  # Per-call timeout, not a global default that someone else may override.
  async with httpx.AsyncClient(timeout=httpx.Timeout(connect=2.0, read=5.0, write=5.0, pool=2.0)) as client:
      response = await client.get("https://inventory.example.com/stock/SKU-1")
  ```

- **Bound database work server-side** so a runaway query cannot consume the whole budget:

  ```python
  from sqlalchemy import text

  async def with_statement_timeout(conn) -> None:
      await conn.execute(text("SET LOCAL statement_timeout = '3s'"))
      await conn.execute(text("SET LOCAL lock_timeout = '1s'"))
  ```

  `SET LOCAL` scopes the setting to the current transaction, so it cannot leak to another
  request that reuses the pooled connection.

- **Never block the event loop.** Move blocking calls to a thread (`await
  run_in_threadpool(...)`, `asyncio.to_thread(...)`) or make the handler synchronous so FastAPI
  runs it in the thread pool. Convert sync database drivers to async ones, or accept sync
  handlers consistently rather than mixing.

- **Move long work out of the request.** Exports, batch imports, and report generation belong in
  a background job with a status endpoint; return `202 Accepted` with a job ID.

- **Fix the query, not the clock.** Add the missing index, paginate, select only needed columns,
  and eliminate N+1 access with eager loading (`selectinload`). Verify with `EXPLAIN` before and
  after.

- **Budget retries as a whole.** If the caller has 10 seconds, three attempts at 5 seconds is a
  design error. Use a total deadline and only retry idempotent operations, with jitter.
- **Order deadlines sensibly:** client > proxy > app handler > downstream call > database
  statement. Each inner layer must fail before its outer layer, or timeouts surface as opaque
  gateway errors.
- **If the work legitimately takes longer**, raise the specific layer's limit deliberately and
  document why; do not raise every timeout globally.
- **Do not disable timeouts, TLS verification, or authentication to "stabilise" latency.**
  Removing a `statement_timeout` converts a fast failure into an unbounded resource hold.

## 6. Verification Steps

1. The previously slow endpoint completes within budget at the target percentile, measured the
   same way as in step 1 of the investigation:

   ```bash
   for i in $(seq 1 20); do
     curl -o /dev/null -sS -w '%{time_total}\n' http://127.0.0.1:8000/orders/search?q=widget
   done | sort -n | tail -n 1
   ```

2. No `504` appears in proxy logs for the endpoint during a load test at expected peak, and the
   application log shows no `httpx.ReadTimeout` for it.
3. The event loop is not blocked: concurrent requests to an unrelated fast endpoint keep their
   latency while the heavy endpoint runs.
4. A terminated client no longer leaves work running: after aborting a request, `pg_stat_activity`
   shows no statement from that request beyond the statement timeout.
5. Downstream calls fail fast in staging when the dependency is made unreachable, returning a
   mapped `503`/`504` JSON error rather than hanging.
6. Side effects are exactly once for a timed-out write followed by a retry (verify with an
   idempotency key or a row count).

## 7. Prevention Strategies

- Publish a written timeout budget per layer and enforce it in code review.
- Track p50/p95/p99 latency per route, with alerting on p99 regression rather than averages.
- Add slow-request logging with stage timings so the next investigation starts with data.
- Load test at peak with realistic dependency latency (injected delay), not just local mocks.
- Guard against N+1 with query-count assertions in tests for high-traffic endpoints.
- Review query plans when data volume changes materially; plan flips are a common silent
  regression.
- Prefer idempotency keys on writes so retries after a timeout are safe.
- Keep cold-start work (connection warm-up, model loading) out of the request path, or exclude it
  from the health-check budget.

## 8. Related Technical Concepts

- Timeout layering and deadline propagation versus per-call timeouts.
- ASGI concurrency, the event loop, thread pools, and `run_in_threadpool`.
- Blocking versus non-blocking I/O and the cost of sync drivers in async code.
- Database statement/lock timeouts and `SET LOCAL` scoping.
- Connection pool checkout latency as a component of request duration (see KB-008).
- Retry semantics: idempotency, backoff with jitter, and retry budgets.
- Tail latency, queueing theory, and why averages hide saturation.
- Graceful shutdown and in-flight request draining.
