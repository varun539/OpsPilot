---
id: KB-008
title: "Database Connection Pool Exhaustion Under Load"
category: database
component: "SQLAlchemy connection pool (sync and async engines)"
severity: high
tags:
  - connection-pool
  - queuepool
  - asyncpg
  - sqlalchemy
  - pool-exhaustion
  - connection-leak
  - pgbouncer
source_type: synthetic
applies_to: "SQLAlchemy >= 2.0, PostgreSQL >= 13, Python >= 3.10"
last_updated: 2026-11-10
related_documents:
  - KB-003
  - KB-005
---

# Database Connection Pool Exhaustion Under Load

## 1. Problem Overview

A database connection is an expensive resource: each one costs a TCP handshake, TLS
negotiation, authentication, and a backend process or slot on the server. Applications
therefore keep a small pool of reusable connections and lend one to each unit of work.

The pool has a hard ceiling. When every connection is lent out and a caller asks for another,
the pool either waits (until a checkout timeout) or raises. That is not a database outage and
not a slow query in isolation — it is **demand exceeding a configured limit**, and the limit can
be exceeded for three different reasons:

1. **Genuine concurrency** — more simultaneous requests than the pool was sized for.
2. **Retention** — connections are held longer than necessary, so a modest request rate
   saturates the pool. Long transactions, an external API call made while holding a session, or
   a response serialised lazily after the session is still open all cause this.
3. **A leak** — a connection is never returned because a `Session()` was not closed on an
   exception path.

The distinction matters because the remedies are different. Raising `pool_size` "fixes"
symptom 1 briefly and makes symptom 3 worse by multiplying leaked connections; the server's
own connection limit then becomes the failure point (see KB-003).

Characteristic exception:

```text
sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached,
connection timed out, timeout 30.00 (Background on this error at: https://sqlalche.me/e/20/3o7r)
```

## 2. Symptoms and Typical Error Messages

**Pool checkout timeout (sync `QueuePool`)**

```text
sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 30.00
```

**Async engine (`AsyncAdaptedQueuePool`)**

```text
sqlalchemy.exc.TimeoutError: QueuePool limit of size 10 overflow 10 reached,
connection timed out, timeout 30.00
```

**Driver-level rejections when the pool is unbounded or bypassed**

```text
asyncpg.exceptions.TooManyConnectionsError: sorry, too many clients already
psycopg2.OperationalError: FATAL: remaining connection slots are reserved for non-replication superuser connections
```

**Server-side exhaustion, visible in PostgreSQL**

```text
FATAL:  sorry, too many clients already
```

**Behavioural symptoms**

- Latency rises smoothly, then requests start failing in a burst once the pool saturates.
- Failures are request-order independent and disappear after a restart — the classic leak
  signature, since a restart returns every leaked connection to the server.
- The app is idle at low traffic and still reports high connection counts on the server.
- Errors appear only on routes that call a slow dependency while holding a session.
- Under a rolling deploy, the *old* and *new* replica sets together exceed the server's limit
  even though each replica alone is within it.

## 3. Possible Root Causes

**Pool sizing versus concurrency**

- `pool_size` and `max_overflow` sized for a developer's laptop, not for workers × threads ×
  expected concurrency. With four workers each holding a pool of 10, the server sees up to 40+
  connections per replica.
- Synchronous handlers running in a thread pool each check out a connection, so concurrency is
  `threads`, not `workers`.
- A burst from a retry storm: failing requests retry immediately, tripling the effective arrival
  rate at the moment the pool is already full.

**Retention**

- A session held open across an external HTTP call, a file upload, or a `sleep`.
- `expire_on_commit=False` plus lazy relationship access during response serialisation, which
  performs queries after the handler returned but while the session is still checked out.
- Explicit transactions wrapping work that does not need atomicity (`BEGIN` early, `COMMIT`
  late), leaving `idle in transaction` backends.
- Streaming responses that hold the session for the lifetime of the stream.

**Leaks**

- `Session()` constructed directly without `close()`, and an exception path skips it:

  ```python
  session = SessionLocal()
  row = session.get(Order, order_id)   # raises -> session never returned to the pool
  ```

- `engine.connect()` used without a `with` block or explicit `close()`.
- A dependency that yields a session without a `finally`, so cancellation (client disconnect)
  never returns the connection.
- Background tasks capturing a session or a request-scoped dependency beyond its lifetime.

**Configuration interactions**

- A second pooler between the app and the database (PgBouncer/Supavisor) with its own small
  limit, so the app's pool size is irrelevant above that ceiling.
- `pool_pre_ping` adding a round trip per checkout under latency, reducing effective throughput
  and lengthening the queue.
- Serverless functions each opening their own pool — N instances × pool size, unbounded.
- `NullPool` used in a hot path, creating a fresh connection per request.

## 4. Step-by-Step Investigation

**Diagnostic only.** Measure before changing any pool parameter.

1. **Confirm the exception is a pool timeout, not a query timeout.** `sqlalchemy.exc.TimeoutError`
   with "QueuePool limit ... reached" is a checkout failure. `DBAPIError` with
   `canceling statement due to statement timeout` is a slow query. They need opposite fixes.

2. **Read the pool state at the moment of failure.** Instrument the pool instead of guessing:

   ```python
   from app.db.session import engine

   pool = engine.pool
   print(type(pool).__name__)
   print("size:", pool.size(), "checked_out:", pool.checkedout(), "overflow:", pool.overflow())
   ```

   A `checkedout` count that never returns to zero after the request finishes is a leak.

3. **Watch server-side activity** to see whether backends are idle, active, or stuck in a
   transaction:

   ```sql
   select state, count(*)
   from pg_stat_activity
   where datname = current_database()
   group by state order by 2 desc;

   select pid, state, wait_event_type, now() - state_change as in_state_for,
          left(query, 120) as query
   from pg_stat_activity
   where datname = current_database() and state <> 'idle'
   order by in_state_for desc;
   ```

   Many `idle in transaction` rows mean retention; many `active` rows mean slow queries; few
   rows with app-side timeouts means the app's pool is the bottleneck, not the server.

4. **Reproduce under load with a controlled ramp** and record latency and error rate:

   ```bash
   # 20 concurrent clients for 30s against one read endpoint
   hey -z 30s -c 20 http://127.0.0.1:8000/orders 2>/dev/null | tail -n 20
   ```

   Note the concurrency level at which the first pool timeout appears; that is your effective
   capacity, which you can compare against configured size × workers.

5. **Test the leak hypothesis directly.** Run a single request in a loop with a short pause,
   then check `checkedout()`. Sequential requests must not accumulate:

   ```python
   import asyncio
   from app.db.session import engine

   async def probe() -> None:
       for _ in range(50):
           async with engine.connect() as conn:
               await conn.exec_driver_sql("select 1")
           assert engine.pool.checkedout() == 0, engine.pool.checkedout()

   asyncio.run(probe())
   ```

6. **Check for exception-path leaks** by forcing a failing request (invalid ID, constraint
   violation) and re-reading `checkedout()`. Leaks concentrate in error paths, not happy paths.

7. **Audit session lifetime in the request flow.** Confirm the session is opened as late as
   possible and closed before serialisation completes; grep for sessions stored on `self`, in
   module globals, or passed into background tasks.

8. **Account for every layer.** Multiply out total possible connections:
   `replicas × workers × (pool_size + max_overflow)` and compare with the server's limit and any
   intermediate pooler's limit. Deploy overlap doubles the multiplier temporarily.

## 5. Safe Troubleshooting Recommendations

Remediation. Fix retention and leaks before increasing size; otherwise you scale the symptom.

- **Use one session per request, scoped by a dependency with guaranteed teardown:**

  ```python
  from collections.abc import AsyncIterator
  from fastapi import Depends
  from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

  engine = create_async_engine(
      settings.database_url,
      pool_size=10,
      max_overflow=5,
      pool_timeout=5,      # fail fast instead of queueing 30s
      pool_recycle=1800,   # recycle before an intermediary drops idle connections
      pool_pre_ping=True,
  )
  SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


  async def get_session() -> AsyncIterator[AsyncSession]:
      async with SessionLocal() as session:
          try:
              yield session
              await session.commit()
          except Exception:
              await session.rollback()
              raise
  ```

  The `async with` guarantees the connection returns to the pool on every path, including
  cancellation.

- **Never hold a session across I/O you do not control.** Fetch what you need, close the
  session, then call the external service. Same for file operations and sleeps.
- **Eagerly load relationships** used by the response model (`selectinload`) inside the session
  so serialisation does not trigger late queries; alternatively close the session before
  returning and use plain data objects.
- **Size deliberately:** choose `pool_size` per worker so that
  `replicas × workers × (pool_size + max_overflow)` stays under the server's limit with
  headroom for deploys and migrations. Verify the server limit; do not assume a default.
- **Set `pool_timeout` low (a few seconds).** A request that waits 30 seconds for a connection
  will usually time out at the proxy anyway and wastes capacity for everyone behind it (see
  KB-005).
- **Add server-side safety nets:** `statement_timeout` and `idle_in_transaction_session_timeout`
  bound the damage from a stuck transaction.
- **Do not disable pooling entirely** to make errors disappear, and do not use an unbounded
  `max_overflow` — it converts a fast app-side failure into a slow server-side one (see KB-003).
- **Never fix exhaustion by shortening or removing authentication/TLS requirements.** The
  connection cost is not where security lives.

## 6. Verification Steps

1. `checkedout()` returns to `0` at idle after the load test, and no growth occurs across
   sequential requests including error cases.
2. The failing scenario now succeeds at the target concurrency, with no `QueuePool limit`
   errors:

   ```bash
   hey -z 30s -c 20 http://127.0.0.1:8000/orders >/dev/null && echo "load ok"
   ```

3. Server-side connection count during the test stays below the limit, with no
   `remaining connection slots` errors:

   ```sql
   select count(*) from pg_stat_activity where datname = current_database();
   ```

4. No connections remain `idle in transaction` for longer than the configured timeout.
5. Latency at peak is within budget, and error rate is zero — not merely shifted into a longer
   timeout.
6. A restart is no longer required to restore service after the scenario; the pool recovers on
   its own.
7. The regression is covered by a test that asserts teardown, for example by asserting the
   session is closed after a handler raises.

## 7. Prevention Strategies

- Standardise a single session dependency; forbid ad-hoc `Session()` construction via lint or
  review.
- Alert on pool saturation *rate* and on `idle in transaction` count, not just on request
  errors.
- Emit pool metrics (`checkedout`, `size`, `overflow`) on a scrape interval so growth is visible
  before exhaustion.
- Load test each release at the expected peak, including deploy-overlap conditions.
- Budget connections per replica in documentation, and re-derive it whenever workers or replica
  counts change.
- Keep transactions short by design: one unit of work per transaction, no external calls inside.
- Recycle connections (`pool_recycle`) below the idle timeout of any intermediary, and keep
  `pool_pre_ping` enabled against managed databases that drop idle connections.
- When a pooler sits between app and database, document both limits and keep the app's total
  below the pooler's.

## 8. Related Technical Concepts

- Connection pool algorithms (queue with overflow), checkout semantics, and `pool_timeout`.
- `QueuePool`, `AsyncAdaptedQueuePool`, `NullPool`, and `StaticPool` trade-offs.
- Session lifecycle, unit of work, and `expire_on_commit`.
- Transaction isolation, `idle in transaction`, and statement timeouts.
- Sync handlers in a thread pool versus async handlers and their concurrency models.
- Serverless connection management and external poolers (see KB-003).
- Backpressure: bounded queues with fast failure versus unbounded waiting.
