# Cache Service

A FastAPI microservice that builds a payload by interleaving two lists of strings after
passing each string through a "transformer" (a stand-in for an external service). Both the
transformer results and the generated payloads are cached in a relational database, so a
string already stored in `transform_cache` is never sent to the transformer again.

## How it works

```
POST /payload ──► digest(list_1, list_2) ──► payload exists? ──yes──► return stored identifier
                                                  │no
                                                  ▼
                                    transform_all(distinct strings)
                                      ├─ cached in transform_cache ──► reuse
                                      └─ missing ──► transformer (concurrent) ──► store
                                                  │
                                                  ▼
                                    interleave, store payload, return identifier
```

Two levels of caching satisfy the "minimize calls to the transformer" requirement:

| Level | Key | Effect |
|-------|-----|--------|
| `payload` | SHA-256 of both input lists | A repeated request returns the original identifier and makes no transformer calls at all |
| `transform_cache` | SHA-256 of a single input string | A new payload only pays for strings that have never been seen, in any request or list |

Within one request, duplicate strings are collapsed before lookup, and the remaining
strings are resolved with a single batched query rather than one query per string. Strings
that are not cached go to the transformer concurrently, up to
`CACHE_SERVICE_TRANSFORMER_MAX_CONCURRENCY` calls at a time.

| Module | Responsibility |
|--------|----------------|
| `api.py` | HTTP endpoints and status codes |
| `payloads.py` | Payload identity, interleaving, deduplication |
| `cache.py` | Transformer cache: batched lookup, write, race handling |
| `transformer.py` | The simulated external service |
| `models.py` / `database.py` | Tables, engine, session lifecycle |
| `cli.py` | `cache-cli` client |

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn cache_service.main:app --reload
```

Interactive API documentation is served at `http://localhost:8000/docs`.

### Docker

```bash
docker compose up --build                   # SQLite on a named volume
```

To run against PostgreSQL instead:

```bash
CACHE_SERVICE_DATABASE_URL=postgresql+asyncpg://cache:cache@postgres:5432/cache \
  docker compose --profile postgres up --build
```

## API

### `POST /payload`

```bash
curl -X POST http://localhost:8000/payload \
  -H 'Content-Type: application/json' \
  -d @sample_input.json
```

```json
{
  "id": "88adbd61-2cd7-4b00-a24f-ff32b3b2424d",
  "message": "Payload created",
  "reused": false
}
```

Returns `201 Created` for a new payload. An identical request returns `200 OK` with the
same identifier and `"reused": true`, because nothing was created.

Both lists must be non-empty, of equal length, and at most 1000 items; violations return
`422` with a description of the problem.

### `GET /payload/{id}`

```bash
curl http://localhost:8000/payload/88adbd61-2cd7-4b00-a24f-ff32b3b2424d
```

```json
{
  "output": "FIRST STRING, OTHER STRING, SECOND STRING, ANOTHER STRING, THIRD STRING, LAST STRING"
}
```

Unknown identifiers return `404`.

### `GET /health`

Returns `{"status": "ok"}`; used as the container health check.

## CLI

`cache-cli` creates a payload and reads it back, repeating the cycle as requested. Its
arguments are parsed and validated by Pydantic Settings.

```bash
cache-cli [--host URL] [-r|--repeat N] [-i|--input FILE|-] [-j|--json JSON]
          [-o|--output FILE|-] [--timeout SECONDS] [-h|--help]
```

Exactly one of `--input` and `--json` supplies the request body. Every argument can also
be set through a `CACHE_CLI_*` environment variable.

```bash
cache-cli --host http://localhost:8000 --json '{"list_1":["a"],"list_2":["b"]}'
cache-cli --input sample_input.json --repeat 3
cat sample_input.json | cache-cli --input - --output report.json
```

The report records each iteration, which makes the caching visible from the command line —
here with the transformer slowed to 200 ms per string. The four strings of the first
request are transformed concurrently, so it takes about one transformer delay:

```json
{
  "host": "http://localhost:8000/",
  "iterations": [
    {"payload_id": "3f74ab9b-…", "reused": false, "elapsed_seconds": 0.223461},
    {"payload_id": "3f74ab9b-…", "reused": true,  "elapsed_seconds": 0.00417},
    {"payload_id": "3f74ab9b-…", "reused": true,  "elapsed_seconds": 0.002286}
  ],
  "output": "ALPHA, GAMMA, BETA, DELTA"
}
```

Exit codes: `0` success, `1` the request failed, `2` invalid arguments or input.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `CACHE_SERVICE_DATABASE_URL` | `sqlite+aiosqlite:///./cache_service.db` | SQLAlchemy URL with an async driver (`sqlite+aiosqlite` or `postgresql+asyncpg`) |
| `CACHE_SERVICE_LOG_LEVEL` | `INFO` | Root log level |
| `CACHE_SERVICE_DATABASE_STARTUP_TIMEOUT_SECONDS` | `10` | How long to wait at startup for the database to accept connections |
| `CACHE_SERVICE_TRANSFORMER_LATENCY_SECONDS` | `0` | Artificial delay per transformer call |
| `CACHE_SERVICE_TRANSFORMER_MAX_CONCURRENCY` | `10` | Transformer calls in flight per request |

## Development

```bash
make check      # ruff + mypy (strict) + pytest with coverage
make test
make lint
make typecheck
```

The suite separates unit tests (`test_hashing`, `test_transformer`, `test_cache`,
`test_payloads`) from integration tests that drive the real ASGI app (`test_api`) and the
CLI end to end against it (`test_cli`). A `transformer_calls` fixture records every call to
the transformer, so the caching requirements are asserted directly rather than inferred.

The suite runs on in-memory SQLite by default. Point it at PostgreSQL to cover transaction
isolation too; the schema is reset before every test:

```bash
CACHE_SERVICE_TEST_DATABASE_URL=postgresql+asyncpg://cache:cache@localhost:5432/cache make test
```

## Design notes and trade-offs

- **Payload identity is content-based.** The identifier is a UUID, but it is looked up by a
  digest of the inputs, so the same request always maps to the same identifier. Digests are
  stored instead of raw strings as keys, which keeps index entries small and bounded.
- **Cache writes use a savepoint, not `session.commit()`.** The cache shares the request's
  session, so a unique conflict must roll back only the cache insert; `session.rollback()`
  would also discard the caller's pending payload work. When the savepoint commits
  differs by database: on PostgreSQL the cache rows commit together with the payload, but
  on SQLite the `sqlite3` driver only opens a transaction at the first write, so releasing
  the savepoint commits the cache rows immediately. SQLAlchemy documents a workaround that
  emits `BEGIN` up front; it was tried and rejected, because every request reads before
  it writes, and under parallel load SQLite then fails the upgrade to a write lock with
  `database is locked` instead of waiting.
- **Concurrent duplicates are resolved by the database.** Both writes are guarded by unique
  constraints. A loser of a payload race rolls back and adopts the committed identifier.
  A loser of a cache race re-reads the winner's rows and inserts only the values that are
  still missing, without calling the transformer again for them.
- **The app owns the engine.** `create_app(engine=...)` stores it on `app.state`, so the
  lifespan and `get_session` use the same database. Tests pass their engine in rather than
  patching `get_session` around a module-level engine created at import time.
- **Startup waits for the database** rather than relying on container ordering, so the
  PostgreSQL profile works without a `depends_on` health gate.
- **The service is async end to end:** handlers, `AsyncSession`, the `aiosqlite` and
  `asyncpg` drivers, and the transformer client. A request waiting on the database or the
  transformer does not occupy a thread, and the strings of one request are transformed
  concurrently: 40 new strings at 200 ms each take about 0.8 s instead of 8 s. Sessions use
  `expire_on_commit=False`, because an async session cannot lazily reload expired
  attributes.
- **Shortcuts taken,** reasonable for an exercise but worth flagging for production:
  - Tables are created at startup instead of through Alembic migrations.
  - Cache entries never expire; the transformer is assumed to be pure and stable. A real
    deployment would version the cache key with the transformer's version.
  - There is no authentication, rate limiting, or pagination over stored payloads.
- **Known limitations,** found by testing and not yet fixed:
  - Concurrent requests that miss the same string each call the transformer: the cache
    stays correct, but the calls are duplicated. With 32 parallel clients sending 200
    requests over 30 distinct strings, the transformer was called 160 times. A
    single-flight guard around the transformer call would fix this.
  - The transformer is called while the request's database transaction is open. With a
    slow remote service, that holds a pooled connection for the duration of the call.
  - The savepoint's commit timing differs between SQLite and PostgreSQL (see above).
  - The default test run uses one in-memory SQLite connection, so it cannot observe
    transaction isolation. The PostgreSQL run that covers it is opt-in, and there is no CI
    to run it automatically.
- **Two ambiguities in the specification** were resolved as follows:
  - `-h` cannot mean both `--host` and `--help`; it is left as `--help` (the convention
    every CLI user expects), so `--host` has no short form.
  - The spec only defines a confirmation for `POST`. Since an identical request creates
    nothing, it returns `200` rather than `201`, and the body carries a `reused` flag.
