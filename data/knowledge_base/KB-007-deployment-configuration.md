---
id: KB-007
title: "Deployment Configuration Failures: Service Starts but Is Unreachable"
category: deployment
component: "Container/platform configuration (ASGI server binding, probes, proxy integration)"
severity: high
tags:
  - deployment
  - docker
  - uvicorn
  - healthcheck
  - 502-bad-gateway
  - proxy-headers
  - root-path
  - reverse-proxy
source_type: synthetic
applies_to: "Uvicorn >= 0.27, FastAPI >= 0.100, nginx >= 1.22, Docker >= 24"
last_updated: 2026-11-10
related_documents:
  - KB-002
  - KB-005
  - KB-001
---

# Deployment Configuration Failures: Service Starts but Is Unreachable

## 1. Problem Overview

A deployment can fail while the application code is correct. The log says `Application startup
complete`, the process is alive, and yet every request returns `502`, `503`, or a proxy error
page. This class of failure lives in the seam between the app and its runtime: network binding,
health probes, path handling behind a proxy, worker model, and startup ordering.

The recurring root idea is **a mismatch between what the platform expects and what the process
does**:

- The platform routes traffic to the container port; the process listens on `127.0.0.1:8000`.
  Inside the container, `localhost` is the container itself, so nothing is reachable — and
  liveness probes fail even though the app "is running".
- The proxy forwards to `/api/orders` with a prefix; the app serves `/orders` and returns `404`
  for everything, or generates redirect URLs and OpenAPI paths without the prefix.
- The probe path is `/health` but the deployment routes it to a different port, or the probe uses
  `HEAD` while only `GET` is defined.
- The build ran migrations in a sidecar that failed, so the app starts against an unmigrated
  schema and returns `500` on first query.

> **Version note:** defaults differ across Uvicorn, Hypercorn, Gunicorn, and platform-injected
> entrypoints — in particular the default bind host, whether proxy headers are honoured by
> default, and how `--forwarded-allow-ips` interacts with `--proxy-headers`. Verify the flags
> with `uvicorn --help` for your pinned version rather than assuming; these defaults have
> changed between releases.

## 2. Symptoms and Typical Error Messages

**Binding and reachability**

```text
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

The app is healthy from inside the container and unreachable from outside:

```bash
docker exec api curl -sS http://127.0.0.1:8000/health   # 200
curl -sS http://localhost:8000/health                    # connection refused
```

```text
ERROR:    [Errno 98] Address already in use
ERROR:    [Errno 13] Permission denied
```

**Proxy responses**

```http
HTTP/1.1 502 Bad Gateway
```
```http
HTTP/1.1 503 Service Unavailable
```
```text
connect() failed (111: Connection refused) while connecting to upstream
upstream timed out (110: Connection timed out) while reading response header from upstream
```

The first indicates nginx could not reach the upstream at all; the second may come from a load
balancer with no healthy targets.

**Probe failures**

```text
Liveness probe failed: Get "http://10.0.0.12:8000/health": dial tcp 10.0.0.12:8000:
connect: connection refused
Readiness probe failed: HTTP probe failed with statuscode: 400
```

A `400` from a probe is often a `Host` header rejection (`TrustedHostMiddleware`) or a missing
required header, not a down app.

**Path and URL symptoms**

- `404` on every route although the app serves them locally.
- Swagger UI loads but every request target is `/orders` instead of `/api/orders`.
- Redirects point at `http://0.0.0.0:8000/...` or an internal hostname.
- Cookies not sent because the generated redirect switched scheme from `https` to `http`.

**Startup ordering**

- `500` on first query immediately after deploy, resolving once migrations run.
- One worker serves new code, another serves old code (mismatched replicas during rollout).
- App exits with code `1` before binding, so the platform reports "container exited".

## 3. Possible Root Causes

**Network binding**

- `--host 127.0.0.1` (or the framework default) inside a container; the runtime must bind
  `0.0.0.0` (or `::`) to accept connections from outside.
- Wrong `--port`, or `PORT` environment variable ignored while the platform routes to it.
- The container exposes a different port than the service object targets.
- A private network or service mesh expecting the app on a different interface.

**Probes**

- Probe path not implemented, or implemented on a different router/prefix.
- Probe method mismatch (`HEAD` versus `GET`).
- Probe timeout shorter than cold-start time, causing a restart loop that never converges.
- Liveness and readiness conflated: a slow dependency makes liveness fail and kills a healthy
  process.

**Proxy integration**

- Missing `--proxy-headers` / trusted forwarded-IP configuration, so `request.url` and
  `redirect_uri` use internal schemes and hosts.
- `root_path` not set when the app is mounted under a prefix, breaking generated docs and
  redirects.
- Proxy buffering or a body-size limit rejecting larger requests with a non-obvious status.
- WebSocket upgrade headers stripped, so streaming endpoints fail while REST works.
- TLS terminated at the proxy but the app generating `http://` URLs.

**Process model**

- Multiple workers with per-process startup side effects (each worker runs lifespan code,
  duplicating scheduler registration or migrations).
- `--workers` greater than available CPU or memory, causing OOM kills mid-request.
- Graceful shutdown timeout shorter than the longest request, cutting in-flight work.
- `latest` image tag with a cached pull, so the "new" deploy runs the old image.

**Configuration and data**

- Environment variables from a different environment (see KB-002).
- Migrations not run, or run twice concurrently by multiple replicas.
- Read-only filesystem breaking uploads, caches, or log writes at startup.
- Seeds/fixtures missing, so the app starts but returns empty or failing responses.

## 4. Step-by-Step Investigation

**Diagnostic only.** Work outward from the process to the edge, proving each hop.

1. **Confirm the process is alive and what it is listening on.**

   ```bash
   docker ps                                   # running? restarting?
   docker logs --tail 50 api                   # startup line, port, errors
   docker exec api sh -c 'netstat -tlnp 2>/dev/null || ss -tlnp'
   ```

   Look for `0.0.0.0:8000` (reachable) versus `127.0.0.1:8000` (not reachable from outside).

2. **Test from inside the container, then from the host, then from outside.** The first hop that
   fails localises the defect:

   ```bash
   docker exec api curl -sS -o /dev/null -w 'inside=%{http_code}\n' http://127.0.0.1:8000/health
   curl -sS -o /dev/null -w 'host=%{http_code}\n' http://127.0.0.1:8000/health
   curl -sS -o /dev/null -w 'edge=%{http_code}\n' https://api.example.com/health
   ```

3. **Verify the probe contract exactly as the platform sends it** — same path, method, port, and
   headers:

   ```bash
   docker exec api curl -i -sS -X GET -H 'Host: api.example.com' http://127.0.0.1:8000/health
   ```

   A `400` here usually means host or header validation, not unavailability.

4. **Inspect the proxy's upstream view.** Read its error log for the connect/read failure class
   and confirm the resolved upstream address and port match the container.

5. **Check path handling end-to-end.** Compare what the proxy forwards with what the route
   expects:

   ```bash
   curl -sS https://api.example.com/openapi.json | head -c 400
   ```

   Paths in `openapi.json` reveal whether `root_path` is applied. Redirect targets reveal
   scheme/host trust issues.

6. **Confirm which image and commit are running.** A stale image explains "the fix didn't work":

   ```bash
   docker inspect --format '{{.Config.Image}} {{index .Config.Labels "org.opencontainers.image.revision"}}' api
   ```

   Compare with the commit you intended to deploy.

7. **Check startup ordering and side effects.** Confirm migrations ran exactly once, and whether
   each worker repeats startup work. Review the deploy logs of any init container or job.

8. **Read exit codes and restart counts.** `137` suggests OOM kill, `1` an application error
   before binding, `143` a termination signal during shutdown.

   ```bash
   docker inspect --format 'exit={{.State.ExitCode}} restarts={{.RestartCount}} oom={{.State.OOMKilled}}' api
   ```

## 5. Safe Troubleshooting Recommendations

Remediation. Change configuration, not application logic, unless the diagnosis points at code.

- **Bind to all interfaces in containers**, keeping the port configurable:

  ```bash
  uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips='*'
  ```

  > **Security note:** `--forwarded-allow-ips='*'` trusts `X-Forwarded-*` from any peer, which is
  > only acceptable when the app is unreachable except through a trusted proxy. Prefer naming the
  > proxy's address or CIDR. Client IPs derived from forwarded headers feed rate limiting and
  > audit logs, so trusting everyone allows spoofing (see KB-006).

- **Implement a dedicated health endpoint** that does not depend on optional systems, and point
  probes at it with a startup grace period longer than cold start. Keep readiness separate: it
  may check the database, liveness must not.

  ```python
  from fastapi import FastAPI

  app = FastAPI()


  @app.get("/health")
  def health() -> dict[str, str]:
      """Liveness: process is up. No dependency checks."""
      return {"status": "ok", "service": "opspilot-api"}
  ```

- **Set `root_path` when mounted under a prefix** (or configure the proxy to strip it
  consistently). Pick one strategy and apply it uniformly; mixing "strip at proxy" with
  `root_path` yields doubled or missing prefixes:

  ```python
  from fastapi import FastAPI

  app = FastAPI(title="OpsPilot API", root_path="/api")
  ```

- **Run migrations as a single ordered step** before new replicas take traffic, using an
  advisory lock so concurrent replicas cannot apply them twice.
- **Pin image tags to an immutable identifier** (commit SHA or digest) so a rollout cannot
  silently reuse a cached `latest`.
- **Set the shutdown grace period above the slowest request** and drain connections; otherwise a
  deploy produces a burst of `502`s (see KB-005).
- **Keep secrets out of images.** Inject them at runtime; a build-time `ARG` for a secret leaks it
  into image layers.
- **Never disable TLS or authentication to make a deployment work.** Terminate TLS at the edge
  with valid certificates and forward the original scheme via trusted headers. Do not switch to
  plaintext HTTP between the proxy and the app across an untrusted network without a documented
  decision.
- Do not loosen host validation or CORS to `*` with credentials enabled; fix the allowed origin
  list instead (see KB-002).

## 6. Verification Steps

1. The service is reachable from outside and returns the documented health payload:

   ```bash
   curl -sS http://localhost:8000/health
   # {"status":"ok","service":"opspilot-api"}
   ```

2. Probes pass with the platform's exact method, path, and headers, and the restart count stays
   at `0` after five minutes of idle.
3. Traffic reaches the intended commit (`org.opencontainers.image.revision` matches the deploy),
   and two consecutive replicas report the same version.
4. Routes resolve correctly through the proxy: a known endpoint returns `200` at the public URL,
   `openapi.json` lists prefixed paths, and redirects use `https` and the public hostname.
5. First query after deploy succeeds, proving migrations completed before traffic.
6. A deploy under light load produces no `502`/`503` burst (graceful shutdown verified).
7. Client IPs in logs reflect real clients, not the proxy address, and rate limiting keys are not
   collapsed (cross-check with KB-006).

## 7. Prevention Strategies

- Codify the deployment contract in one place: bind address, port variable, probe path and
  method, `root_path`, worker count, and grace period.
- Add a post-deploy smoke test in CI that calls the public health endpoint and one real route
  through the proxy, failing the pipeline on non-`2xx`.
- Use immutable image tags and record the revision label so "which code is running" is always
  answerable.
- Keep liveness cheap and independent; add dependency checks only to readiness.
- Test the container image locally with the same bind flags and port env var the platform uses —
  not with `uvicorn --reload` on the host.
- Review proxy configuration as code and include forwarded-header trust in security review.
- Document rollback: previous image tag, migration reversibility, and expected blast radius.

## 8. Related Technical Concepts

- Container networking, port publishing, and the difference between `127.0.0.1` and `0.0.0.0`.
- ASGI servers, worker models, and lifespan startup/shutdown semantics.
- Reverse proxy upstream resolution, `root_path`, and URL generation behind a prefix.
- Trusted proxy headers and client-IP derivation.
- Liveness versus readiness versus startup probes.
- Immutable artifacts, digests, and rollout strategies.
- Graceful shutdown, connection draining, and in-flight request handling.
- Migration ordering, advisory locks, and zero-downtime schema changes.
