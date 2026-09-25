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
                               read phase: look up the distinct strings in transform_cache,
                                           then end the transaction (connection released)
                                                  │
                                                  ▼
                               transformer: only the missing strings, concurrently,
                                            sharing calls with other in-flight requests
                                                  │
                                                  ▼
                               write phase: store results (savepoint), store payload, commit
```

Two levels of caching satisfy the "minimize calls to the transformer" requirement:

| Level | Key | Effect |
|-------|-----|--------|
| `payload` | SHA-256 of both input lists | A repeated request returns the original identifier and makes no transformer calls at all |
| `transform_cache` | SHA-256 of a single input string | A new payload only pays for strings that have never been seen, in any request or list |

Within one request, duplicate strings are collapsed before lookup, and the remaining
strings are resolved with a single batched query rather than one query per string. Strings
that are not cached go to the transformer concurrently. At most
`CACHE_SERVICE_TRANSFORMER_MAX_CONCURRENCY` calls are in flight across the whole process,
and concurrent requests that need the same string share one call.

| Module | Responsibility |
|--------|----------------|
| `api.py` | HTTP endpoints, status codes, liveness and readiness |
| `payloads.py` | Payload identity, interleaving, the read / transform / write phases |
| `cache.py` | Transformer cache: batched lookup, ordered write, race handling |
| `transformer.py` | The simulated external service, and the client that limits and shares calls |
| `models.py` / `database.py` | Tables, engine, session lifecycle |
| `main.py` | `create_app` factory: settings, engine and transformer client per app |
| `cli.py` | `cache-cli` client |

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
make install        # pinned versions from requirements-dev.lock
make run            # uvicorn --factory cache_service.main:create_app --reload
```

The app is built by a factory, so importing the package reads no settings and creates
no engine.

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

Both lists must be non-empty, of equal length, and at most 1000 items, and each string
at most 1000 characters; violations return `422` with a description of the problem. The
specification sets no limits; these bound the memory, storage and transformer work one
request can cause.

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

### `GET /health` and `GET /ready`

`/health` is liveness: it returns `{"status": "ok"}` whenever the process serves requests.
`/ready` also checks that the database accepts connections and returns `503` otherwise; the
container health check uses it.

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
| `CACHE_SERVICE_TRANSFORMER_MAX_CONCURRENCY` | `10` | Transformer calls in flight across the process |

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

Three tests only run there: savepoint isolation, two writers storing the same strings in
opposite orders, and concurrent identical requests on independent sessions.

CI (`.github/workflows/ci.yml`) runs lint, type checks and the suite on SQLite, the suite
again on PostgreSQL, and builds the image and waits for `/ready`. Dependencies are pinned
in `requirements.lock` (the image) and `requirements-dev.lock` (development and CI);
`make lock` regenerates both after `pyproject.toml` changes.

## Design notes and trade-offs

- **Payload identity is content-based.** The identifier is a UUID, but it is looked up by a
  digest of the inputs, so the same request always maps to the same identifier. Digests are
  stored instead of raw strings as keys, which keeps index entries small and bounded.
- **A request works in three phases.** It reads the payload and cache rows, ends that
  read-only transaction, calls the transformer with no connection held, and then writes
  cache rows and payload in one short transaction. A slow transformer therefore does not
  keep pooled connections idle in a transaction.
- **The cache never commits or rolls back.** `load_cached` and `store` run inside the
  caller's transaction, and only `payloads.py`, which owns the unit of work, ends it.
- **Cache writes use a savepoint.** A unique conflict must roll back only the cache insert;
  `session.rollback()` would also discard the caller's pending payload work. When the savepoint commits
  differs by database: on PostgreSQL the cache rows commit together with the payload, but
  on SQLite the `sqlite3` driver only opens a transaction at the first write, so releasing
  the savepoint commits the cache rows immediately. SQLAlchemy documents a workaround that
  emits `BEGIN` up front; it was tried and rejected, because every request reads before
  it writes, and under parallel load SQLite then fails the upgrade to a write lock with
  `database is locked` instead of waiting.
- **Concurrent duplicates are resolved by the database.** Both writes are guarded by unique
  constraints. A loser of a payload race rolls back and adopts the committed identifier.
  A loser of a cache race re-reads the winner's rows, keeps them, and inserts only the
  values that are still missing, without calling the transformer again for them.
- **Cache rows are inserted in digest order.** On PostgreSQL an insert locks each new key
  until the transaction ends. Two requests inserting the same new strings in different
  orders used to deadlock, and one of them failed with a 500; a fixed order rules that out.
- **Transformer calls are shared and limited per process.** `TransformerClient` lives on
  the app. Concurrent requests that need the same string wait for one call, the limit
  covers all requests together, and when one call fails the request's other calls are
  cancelled rather than left running. A shared call is cancelled only when no request
  waits for it any more.
- **The app owns its settings and engine.** `create_app(settings, engine)` builds the
  engine and transformer client from the settings it is given and stores them on
  `app.state`, so the lifespan, `get_session` and the handlers all use the same ones.
  Tests pass their own instead of patching module-level objects.
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
- **Known limitations:**
  - Calls are shared within one process. With several uvicorn workers or replicas, each
    process may call the transformer for the same new string once; the unique constraint
    keeps the cache correct.
  - Requests only share a call while it is in flight. One that arrives just after a call
    finished, but before its row was committed, calls the transformer again.
  - The savepoint's commit timing differs between SQLite and PostgreSQL (see above).
- **Two ambiguities in the specification** were resolved as follows:
  - `-h` cannot mean both `--host` and `--help`; it is left as `--help` (the convention
    every CLI user expects), so `--host` has no short form.
  - The spec only defines a confirmation for `POST`. Since an identical request creates
    nothing, it returns `200` rather than `201`, and the body carries a `reused` flag.
