---
id: KB-006
title: "HTTP 429 Too Many Requests: Diagnosing and Tuning API Rate Limiting"
category: api-reliability
component: "Rate limiting layer (edge proxy, API gateway, and FastAPI middleware)"
severity: medium
tags:
  - rate-limiting
  - http-429
  - retry-after
  - token-bucket
  - redis
  - throttling
  - backoff
  - api-gateway
source_type: synthetic
applies_to: "FastAPI >= 0.100, Python >= 3.10, Redis >= 6"
last_updated: 2026-11-10
related_documents:
  - KB-005
  - KB-004
---

# HTTP 429 Too Many Requests: Diagnosing and Tuning API Rate Limiting

## 1. Problem Overview

`429 Too Many Requests` means a limiter decided the caller has exhausted its quota for the
current window. Unlike `503`, which signals that the server cannot serve anyone, `429` is
caller-specific and often temporary: the same request will succeed later.

Rate limiting exists to protect three things: shared capacity (no single client starves
others), cost (paid downstream calls per tenant), and security (credential stuffing and
scraping). Because those goals conflict, limiters are frequently mis-tuned — too tight and
legitimate bursty clients fail; too loose and the limit changes nothing.

Two properties make `429` incidents confusing:

- **Limits are rarely enforced in one place.** An edge (CDN, ingress, API gateway) and the
  application may both limit, with different keys and windows. A client can be well within the
  app's quota while the edge has already rejected it.
- **The identity used for the key may be wrong.** Limiting by client IP behind a proxy can
  collapse every user into the proxy's address, or spread one user across many addresses.

Correct behaviour requires the response to be *actionable*: a `Retry-After` header (seconds or
an HTTP date), and ideally the limit and remaining counters so clients can self-throttle rather
than discover limits by failing.

## 2. Symptoms and Typical Error Messages

**Response shape**

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 12
RateLimit-Limit: 100
RateLimit-Remaining: 0
RateLimit-Reset: 12
Content-Type: application/json

{"detail": "Rate limit exceeded. Try again in 12 seconds."}
```

> **Note:** `RateLimit-*` header names have gone through draft revisions in the IETF process.
> Treat the exact header set as deployment-specific and confirm what your gateway emits rather
> than assuming a standard.

**Server-side log signals**

- Limiter counters at their ceiling for a single key across many requests.
- A sudden increase in rejected requests immediately after a deploy (limit changed, or key
  derivation changed).
- All rejections sharing one IP address or one tenant ID — a sign of key collapse.
- Rejections concentrated on a single endpoint with a high per-request cost.

**Client-visible symptoms**

- `429` on the first request of a session, then success after a pause.
- `429` for one user while others are fine (per-key limit), or for everyone at once (global or
  mis-keyed limit).
- `429` only from certain regions — often an edge in front of one region applying a separate
  policy.
- Retry storms: a client retries immediately, gets `429` again, and amplifies load.
- `429` during batch jobs or parallel fan-out, where a legitimate workload exceeds a limit
  designed for interactive traffic.

**Confusing neighbours**

- `503` from the same endpoint means the service is failing, not that you are over quota.
- `403` from a WAF may indicate a bot rule rather than a limiter.
- A `429` without `Retry-After` forces clients to guess, which usually means they guess
  "immediately".

## 3. Possible Root Causes

**Key derivation**

- Limiting by socket peer address behind a proxy, so all traffic is attributed to the proxy.
  Trusting `X-Forwarded-For` unconditionally has the opposite failure: clients can forge it and
  bypass limits entirely.
- Missing authentication context: unauthenticated requests all collapse onto one bucket.
- Key includes a volatile value (session ID, full URL with query string), so limits never bind
  and effectively do not exist.

**Algorithm and window**

- Fixed windows allow a burst of `2 × limit` at a boundary (limit consumed at the end of one
  window and the start of the next). Users perceive this as random rejection.
- Token buckets sized for average rate but with a bucket depth smaller than legitimate bursts.
- Per-request-cost endpoints (search, export, LLM calls) limited by request count rather than
  by cost, so a few expensive calls exhaust shared capacity.

**Distributed state**

- Each app replica counts in memory, so the effective limit is `replicas × limit`; behaviour
  changes with autoscaling.
- Redis-backed limiter with non-atomic increment-then-expire, allowing a permanent key with no
  TTL and a self-inflicted permanent block.
- Limiter store outage handled by failing *closed* — a Redis blip becomes a total outage.
- Clock differences between replicas causing window boundaries to disagree.

**Configuration and layering**

- Edge and application limits that multiply: 100/min at the gateway plus 100/min in the app
  behaves like 100/min, but a per-IP edge limit of 50 can reject before the tenant-level app
  limit is reached.
- Limits applied to health checks, metrics scrapes, or preflight `OPTIONS`, causing monitoring
  alerts and CORS failures.
- A limit lowered for a DDoS event and never restored.

## 4. Step-by-Step Investigation

**Diagnostic only.** Identify *which* limiter rejected the request before changing any number.

1. **Determine the rejecting layer.** Send one request and inspect headers that identify the
   edge (server header, gateway request ID, vendor-specific counters). Then call the app
   directly, bypassing the edge:

   ```bash
   curl -i -sS https://api.example.com/orders -H "Authorization: Bearer ${TOKEN}" | head -n 15
   curl -i -sS http://127.0.0.1:8000/orders -H "Authorization: Bearer ${TOKEN}" | head -n 15
   ```

   Direct success with edge rejection means the limit is upstream, and app-side tuning is
   irrelevant.

2. **Confirm the limit and window from configuration, not from guesses.** Locate the limiter
   config for both layers and record `limit`, `window`, and `key` for each. Multiply layers that
   apply to the same request.

3. **Identify the actual key.** Log the derived key with a hash prefix, never the raw credential:

   ```python
   import hashlib

   def limit_key(request) -> str:
       subject = request.state.user_id or request.client.host
       return hashlib.sha256(subject.encode()).hexdigest()[:16]
   ```

   If every request maps to the same key, the limiter is effectively global.

4. **Reproduce the throttle deterministically** with a burst and observe the counter progression:

   ```bash
   for i in $(seq 1 60); do
     curl -sS -o /dev/null -w '%{http_code} ' http://127.0.0.1:8000/health
   done; echo
   ```

   Recording the status sequence reveals the limit, the window behaviour, and whether the
   boundary allows a double burst.

5. **Check for the fixed-window boundary artefact.** Fire `limit` requests just before a window
   boundary and `limit` more just after. `2 × limit` accepted in a short span confirms a fixed
   window rather than a sliding or token-bucket implementation.

6. **Verify distributed correctness.** Hit two replicas alternately and confirm they share one
   counter. Independent counters mean in-memory state (or per-replica Redis keys).

7. **Inspect limiter store health and TTLs.** Look for keys without expiry, and for latency on
   limiter calls (a slow limiter adds latency to every request, including allowed ones):

   ```bash
   redis-cli --scan --pattern 'rl:*' | head -n 20
   redis-cli ttl 'rl:tenant:acme'
   redis-cli --latency -h 127.0.0.1
   ```

8. **Correlate with client behaviour.** Inspect access logs for retry patterns (same request
   repeated within milliseconds), fan-out concurrency, and per-client request rates. A client
   ignoring `Retry-After` is a client defect, but it is also a capacity risk you must contain.

## 5. Safe Troubleshooting Recommendations

Remediation. Change one dimension at a time: key, then limit, then algorithm.

- **Fix the key before the number.** Limit on the most specific stable identity available:
  authenticated subject or API key, falling back to IP only when the proxy header is trusted.
  In FastAPI/Starlette, enable proxy-header handling only from known proxies, never globally.

  ```python
  from fastapi import FastAPI, Request
  from starlette.middleware.trustedhost import TrustedHostMiddleware

  app = FastAPI()
  app.add_middleware(TrustedHostMiddleware, allowed_hosts=["api.example.com"])
  # Configure the ASGI server or proxy to trust X-Forwarded-* only from known edge addresses.
  ```

- **Adopt a burst-tolerant algorithm.** A token bucket absorbs short spikes while enforcing a
  long-run rate; a sliding window avoids boundary doubling. Reserve fixed windows for cases
  where strict per-minute accounting is a contractual requirement.

- **Make limits atomic and bounded.** Increment and set expiry in one operation, and always
  attach a TTL so a bug cannot create a permanent block:

  ```python
  import time

  ALLOWED = 100
  WINDOW = 60  # seconds

  def allow(redis, key: str, now: float | None = None) -> tuple[bool, int]:
      now = now if now is not None else time.time()
      window_start = int(now // WINDOW)
      bucket = f"rl:{key}:{window_start}"
      # Pipeline keeps INCR + EXPIRE atomic with respect to other clients in practice.
      pipe = redis.pipeline()
      pipe.incr(bucket)
      pipe.expire(bucket, WINDOW + 5)
      count, _ = pipe.execute()
      remaining = max(0, ALLOWED - int(count))
      return int(count) <= ALLOWED, remaining
  ```

  > **Version note:** `redis-py` pipelines are transactional by default but retries must be
  > considered; for strict multi-key correctness use a Lua script or `EVALSHA`. In production,
  > prefer a maintained limiter library and verify its atomicity guarantees for your version.

- **Return actionable headers.** Include `Retry-After` computed from the window reset, and
  avoid advertising headers the limiter cannot honour.

- **Choose fail-open deliberately for the limiter store, with a fallback cap.** If Redis is
  unreachable, either degrade to a conservative local limit or allow traffic and alert — but
  decide explicitly. Do not let a shared cache outage reject all requests.

- **Separate interactive from batch traffic** with distinct quotas or a burst allowance for
  known clients, rather than raising the global limit for everyone.

- **Exclude health checks, metrics, and `OPTIONS` preflight** from limits that would break
  monitoring or browsers.

- **Never remove rate limits entirely to stop `429`s**, and never exempt an unauthenticated
  endpoint from all limiting. If limits must change temporarily, record the change and its
  revert time.

- Clients should honour `Retry-After` and use exponential backoff with jitter (see KB-005).
  Server-side, consider a short `Retry-After` floor to prevent thundering herds.

## 6. Verification Steps

1. Requests within the limit succeed and headers report a positive remaining count:

   ```bash
   curl -i -sS http://127.0.0.1:8000/orders -H "Authorization: Bearer ${TOKEN}" \
     | grep -iE 'HTTP/|ratelimit|retry-after'
   ```

2. Exceeding the limit returns `429` **with** `Retry-After`, and the same request succeeds after
   waiting that interval.
3. Distinct subjects get independent buckets; one exhausted key does not affect another. Test
   with two tokens.
4. Under a two-replica run, the aggregate limit is enforced exactly once, not doubled.
5. `health` and `OPTIONS` requests are not limited out, and monitoring stays green.
6. Limiter keys all carry a TTL (`redis-cli ttl` returns a positive number, never `-1`).
7. A limiter-store outage leaves the API serving (per the chosen fail-open/fallback policy), as
   verified by pointing the limiter at an unavailable address in staging.

## 7. Prevention Strategies

- Document each layer's limit, key, and window in one table; review it whenever a new proxy or
  gateway is introduced.
- Treat limit changes as configuration changes: versioned, reviewed, with a rollback path.
- Emit metrics for allowed/rejected counts per key class, plus limiter latency and store errors.
- Load test with realistic client fan-out so limits are tuned against observed traffic, not
  intuition.
- Publish client guidance (backoff with jitter, honour `Retry-After`) and include it in API
  documentation.
- Alarm on rejection-rate spikes broken down by key, which distinguishes a noisy client from a
  global misconfiguration.
- Test limiter behaviour in integration tests, including the boundary case and store outage.

## 8. Related Technical Concepts

- Token bucket, leaky bucket, fixed window, and sliding window counters.
- Distributed rate limiting with Redis, atomic operations, and TTLs.
- Proxy header trust (`X-Forwarded-For`, `Forwarded`) and IP spoofing risk.
- `Retry-After` semantics, exponential backoff with jitter, and retry budgets.
- Quotas versus throttling versus concurrency limits.
- WAF and bot-management rules that can also emit `429`.
- Cost-based limiting for expensive endpoints versus request-count limiting.
