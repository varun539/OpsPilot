---
id: KB-003
title: "Connecting to Supabase-Hosted PostgreSQL: Failures and Configuration Traps"
category: database
component: "PostgreSQL network connectivity (Supabase-managed Postgres)"
severity: high
tags:
  - supabase
  - postgresql
  - connection-refused
  - connection-string
  - pgbouncer
  - supavisor
  - ipv6
  - ssl
source_type: synthetic
applies_to: "PostgreSQL >= 13, SQLAlchemy >= 2.0, asyncpg >= 0.29"
last_updated: 2026-11-10
related_documents:
  - KB-008
  - KB-002
---

# Connecting to Supabase-Hosted PostgreSQL: Failures and Configuration Traps

## 1. Problem Overview

A managed PostgreSQL endpoint such as Supabase exposes the database in more than one way, and
the choices are not interchangeable. A deployment can reach one route and not another, and the
resulting errors are easily mistaken for application bugs.

Three independent layers must all succeed before a single row can be read:

1. **Network reachability** — DNS resolves the host to an address family your runtime can use,
   and TCP to the port is not blocked by a firewall, security group, or egress policy.
2. **Identity and transport** — the username/password (or IAM-style credential) is valid for
   that host and the connection is encrypted as the server requires.
3. **Protocol compatibility** — the driver speaks a mode the endpoint supports. A transaction
   mode pooler, for example, multiplexes connections between statements and therefore breaks
   assumptions a driver makes about session-scoped state.

> **Provider note:** Supabase publishes several connection routes — a direct database
> connection and a connection pooler in session and transaction modes, on different hostnames
> and ports — and the exact hostnames, ports, and whether direct connections resolve to IPv4
> or IPv6 depend on project age and region. **Do not rely on memorised values.** Copy the
> connection strings from the project dashboard's connection settings, and confirm current
> behaviour against the provider's live documentation before drawing conclusions.

The most common real-world failure is not a wrong password: it is a correct connection string
*pasted from the wrong row* of that settings page, or a direct-connection host used from a
runtime that only has IPv6 egress disabled.

## 2. Symptoms and Typical Error Messages

**Name resolution / address family**

```text
socket.gaierror: [Errno -2] Name or service not known
asyncpg.exceptions.InvalidCatalogNameError  # sometimes misreported under pooler misrouting
```

```text
psycopg2.OperationalError: could not translate host name "db.example.supabase.co" to address:
Temporary failure in name resolution
```

A hostname that resolves to an IPv6 address in an environment without IPv6 egress typically
appears as `Network is unreachable` or a connection timeout with no response — not as a DNS
error. This asymmetry is the usual reason "the same URL works on my laptop but not in CI".

**TCP / firewall**

```text
psycopg2.OperationalError: could not connect to server: Connection refused
    Is the server running on host "..." and accepting TCP/IP connections on port 5432?
```

```text
TimeoutError: [Errno 110] Connection timed out
```

Refused means something answered and rejected the connection (wrong port, service down,
listener not exposed); timed out usually means a firewall dropped the packets silently.

**Authentication and TLS**

```text
FATAL:  password authentication failed for user "postgres"
FATAL:  no pg_hba.conf entry for host "203.0.113.10", user "postgres", database "postgres", SSL off
FATAL:  Tenant or user not found
FATAL:  server does not support SSL, but SSL was required
```

**Pooler-specific**

```text
FATAL:  connection to server at "..." failed: FATAL: remaining connection slots are reserved
```
```text
asyncpg.exceptions.PostgresSyntaxError: cannot insert multiple commands into a prepared statement
```
```text
InvalidSQLStatementNameError: prepared statement "__asyncpg_stmt_3__" does not exist
```

The last two are the signature of a driver using prepared statements against a transaction-mode
pooler, where each statement may be routed to a different backend connection.

**Application-level symptoms**

- Works from a developer machine, fails from a container or serverless function.
- `pytest` passes against a local database, deployed app returns `500` on the first query
  (see KB-001).
- Connections succeed but every write is lost — you are pointed at a different database or a
  read-only replica.
- Intermittent `Too many connections` under moderate load; see KB-008 for the pooling view.

## 3. Possible Root Causes

**Connection string construction**

- Wrong scheme: `postgres://` is accepted as a legacy alias by some tools but SQLAlchemy 2.x
  requires `postgresql://`, and an async driver requires `postgresql+asyncpg://` (or
  `postgresql+psycopg://` for psycopg 3).
- Special characters in the password (`@`, `:`, `/`, `#`, `?`, `%`) not percent-encoded, so the
  parser splits the URL at the wrong place. This is the single most common "the password is
  right but auth fails" cause.
- Copy-paste from a dashboard that displays `[YOUR-PASSWORD]` as a literal placeholder.
- Extra whitespace or quotes retained from a shell/YAML value (see KB-002).
- Host/port taken from the direct connection but the username taken from the pooler — a mixed
  string that authenticates against neither.

**Route selection**

- Direct connection used from an IPv4-only environment when the project's direct host resolves
  to IPv6.
- Transaction-mode pooler used with a driver or ORM configured for prepared statements,
  `LISTEN/NOTIFY`, advisory locks, or session-level `SET` — all of which assume a stable
  backend connection.
- Serverless or autoscaling workloads opening a connection per invocation against the direct
  endpoint, exhausting the server's connection limit.

**Network policy**

- Egress firewall allows `443` only; the database port is not allowed.
- The platform's IP allow-list does not include the deployment's NAT/egress address, which may
  change for autoscaled runners.
- Private networking enabled, with the client still using the public hostname (or vice versa).

**TLS**

- `sslmode` not specified while the server refuses plaintext, or a custom CA not provided in a
  locked-down environment.
- A proxy intercepting TLS with an untrusted certificate.

## 4. Step-by-Step Investigation

**Diagnostic only.** Do not print full connection strings — they contain the password. Redact
user and host when sharing.

1. **Prove where it fails: DNS, TCP, TLS, auth, or protocol.** Move outward-in with one tool per
   layer:

   ```bash
   # 1. DNS: which address families does the host return?
   getent ahosts db.example.supabase.co | awk '{print $1}' | sort -u

   # 2. TCP: can this runtime open the port at all?
   timeout 5 bash -c '</dev/tcp/db.example.supabase.co/5432' && echo "tcp ok" || echo "tcp blocked"

   # 3. TLS: is the handshake accepted?
   openssl s_client -connect db.example.supabase.co:5432 -starttls postgres -brief </dev/null
   ```

   If step 1 returns only IPv6 addresses and step 2 fails, the cause is address family, not
   credentials — and no amount of password editing will help.

2. **Confirm which route the string points at.** Parse the URL *without* echoing the password
   and compare host, port, and mode against the dashboard:

   ```python
   from sqlalchemy.engine import make_url

   url = make_url("postgresql+asyncpg://user:REDACTED@host:6543/postgres")
   print(url.drivername, url.host, url.port, url.database, url.query)
   ```

   Note whether the port corresponds to the direct endpoint or the pooler, and whether the
   query string carries pooler-required options.

3. **Reproduce with a minimal client** so the ORM is not in the path:

   ```bash
   psql "host=... port=... dbname=postgres user=... sslmode=require" -c 'select 1'
   ```

   Success here with failure in the app isolates the defect to the application's URL handling
   or driver configuration.

4. **Confirm identity and target database** once connected:

   ```sql
   select current_user, current_database(), inet_server_addr(), version();
   ```

   This catches "connected to the wrong database" and "connected to a replica" immediately.

5. **Verify TLS is actually on.** `sslmode=require` is a client-side request; confirm what the
   session negotiated:

   ```sql
   select ssl, version from pg_stat_ssl where pid = pg_backend_pid();
   ```

6. **Test the pooler mode against the workload's needs.** If the endpoint is a transaction-mode
   pooler, disable driver-side prepared statements and session state, then re-run the failing
   query. A query that succeeds in session mode and fails in transaction mode confirms the
   mode mismatch rather than a data problem.

7. **Check server-side connection accounting** for exhaustion or rejected logins:

   ```sql
   select state, count(*) from pg_stat_activity group by state order by 2 desc;
   select count(*) from pg_stat_activity where usename = current_user;
   ```

8. **Compare environments** by diffing *key names and host/port/mode only*, never values. A
   difference in route between staging and production explains many "only prod fails" reports.

## 5. Safe Troubleshooting Recommendations

Remediation, in order of preference. Changing route or relaxing transport security are last
resorts, not first moves.

- **Use the connection string the provider generates for the route you intend.** Re-copy it
  rather than hand-editing, then substitute the password at injection time — do not commit a
  string containing it.
- **Percent-encode the password** when constructing the URL manually, or better, pass it as a
  separate parameter so no encoding is needed:

  ```python
  from sqlalchemy.engine import URL

  url = URL.create(
      "postgresql+asyncpg",
      username="opspilot_app",
      password=os.environ["DATABASE_PASSWORD"],   # never logged
      host="db.example.supabase.co",
      port=5432,
      database="postgres",
      query={"ssl": "require"},
  )
  ```

- **Match the route to the workload.** Long-lived servers with moderate concurrency can use a
  direct connection with a bounded pool; serverless, edge, or highly concurrent services should
  use the provider's pooler. Verify the mode in the dashboard before choosing.
- **For transaction-mode pooling, disable prepared statements** in the driver and avoid
  session-scoped features:

  ```python
  from sqlalchemy.ext.asyncio import create_async_engine

  engine = create_async_engine(
      url,
      pool_pre_ping=True,
      connect_args={"statement_cache_size": 0, "server_settings": {"application_name": "opspilot-api"}},
  )
  ```

  > **Version note:** the exact keyword names differ between `asyncpg`, `psycopg`, and
  > `psycopg2`, and `statement_cache_size` is asyncpg-specific. Confirm the parameter for your
  > driver version; on psycopg 3 the equivalent is disabling automatic prepared statements.

- **Fix address-family problems by choosing a route that offers IPv4** (the pooler, per the
  dashboard) or by enabling IPv6 egress. Do not disable TLS, and do not set
  `sslmode=disable`/`ssl=false` to "get it connecting" — that exposes credentials and data on
  the wire and the server may reject it regardless.
- **Keep TLS verification on.** If a private CA is in use, install the CA in the runtime's
  trust store; do not pass a blanket "don't verify" flag.
- **Never hard-code credentials in source, images, or logs.** Rotate the database password
  through the provider dashboard and update the secret manager; see KB-002.
- **If the platform requires an IP allow-list, use a stable egress address** (NAT gateway) so
  autoscaling does not silently lose access.

## 6. Verification Steps

1. The application connects with the deployed configuration, from inside the deployed network:

   ```bash
   python - <<'PY'
   import asyncio
   from sqlalchemy import text
   from app.db.session import engine

   async def main() -> None:
       async with engine.connect() as conn:
           print((await conn.execute(text("select 1"))).scalar_one())

   asyncio.run(main())
   PY
   # expect: 1
   ```

2. The session is encrypted and the target is correct:

   ```sql
   select ssl from pg_stat_ssl where pid = pg_backend_pid();   -- expect: t
   select current_database();                                   -- expect the intended database
   ```

3. A representative write lands where it should (verify in the dashboard's table editor, not
   only via the app's own read path).
4. The failing endpoint returns `2xx`, not `500`, and logs contain no driver tracebacks.
5. Connection count stays inside the server's limit during a short load test; no
   `remaining connection slots` errors appear.
6. Credentials do not appear in the repository or logs: `git grep -i 'postgresql://'` returns
   only placeholder examples, and startup logs redact the password.

## 7. Prevention Strategies

- Store one canonical connection string per environment in the secret manager, with a comment
  naming which route (direct/session/transaction) it uses and why.
- Assert the driver scheme in code at startup (`postgresql+asyncpg`, not `postgres`) so a wrong
  scheme fails immediately with a clear message.
- Keep a `/health/db` check or startup connectivity probe that distinguishes "cannot resolve",
  "cannot connect", and "cannot authenticate", and log them as different events.
- Pin the provider's connection guidance in the runbook, with a note to re-check it when the
  project is migrated or upgraded, since hostnames and address families can change.
- Write integration tests against a disposable database using the same driver and pooling mode
  as production; do not test only against local SQLite or a direct-connection dev database.
- Add a query timeout and a connection acquisition timeout so a broken route fails in seconds
  instead of hanging until the request times out (see KB-005).
- Monitor connection counts and authentication failures; a rising auth-failure rate is the
  earliest signal of a rotated or truncating credential.

## 8. Related Technical Concepts

- PostgreSQL wire protocol, `pg_hba.conf` rules, and `sslmode` semantics.
- Direct connections versus PgBouncer/Supavisor pooling, and session versus transaction mode.
- Prepared statements and why multiplexing breaks session-scoped state.
- IPv4/IPv6 dual stack, AAAA records, and egress filtering in container platforms.
- URL percent-encoding and SQLAlchemy `URL.create` versus string URLs.
- Least-privilege database roles and credential rotation.
- Read replicas and their read-only enforcement, which surfaces as write errors rather than
  connection errors.
