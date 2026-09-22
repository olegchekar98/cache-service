# Cache Service

A FastAPI microservice that builds a payload by interleaving two lists of strings after
passing each string through a "transformer" (a stand-in for an external service). Both the
transformer results and the generated payloads are cached in a relational database, so the
transformer is called at most once per distinct string, ever.

## How it works

```
POST /payload ──► digest(list_1, list_2) ──► payload exists? ──yes──► return stored identifier
                                                  │no
                                                  ▼
                                    transform_all(distinct strings)
                                      ├─ cached in transform_cache ──► reuse
                                      └─ missing ──► transformer ──► store
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
strings are resolved with a single batched query rather than one query per string.

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
CACHE_SERVICE_DATABASE_URL=postgresql+psycopg://cache:cache@postgres:5432/cache \
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
here with the transformer slowed to 200 ms per string:

```json
{
  "host": "http://localhost:8000/",
  "iterations": [
    {"payload_id": "438e2cdd-…", "reused": false, "elapsed_seconds": 0.8448},
    {"payload_id": "438e2cdd-…", "reused": true,  "elapsed_seconds": 0.004563},
    {"payload_id": "438e2cdd-…", "reused": true,  "elapsed_seconds": 0.004347}
  ],
  "output": "ALPHA, GAMMA, BETA, DELTA"
}
```

Exit codes: `0` success, `1` the request failed, `2` invalid arguments or input.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `CACHE_SERVICE_DATABASE_URL` | `sqlite:///./cache_service.db` | SQLAlchemy database URL |
| `CACHE_SERVICE_LOG_LEVEL` | `INFO` | Root log level |
| `CACHE_SERVICE_TRANSFORMER_LATENCY_SECONDS` | `0` | Artificial delay per transformer call |

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

## Design notes and trade-offs

- **Payload identity is content-based.** The identifier is a UUID, but it is looked up by a
  digest of the inputs, so the same request always maps to the same identifier. Digests are
  stored instead of raw strings as keys, which keeps index entries small and bounded.
- **The transformer cache is committed before the payload.** Transformer results are the
  expensive artefact; committing them separately means they survive a later failure and
  become visible to concurrent requests immediately.
- **Concurrent duplicates are resolved by the database.** Both writes are guarded by unique
  constraints; a loser of the race rolls back and adopts the committed row rather than
  failing the request. This is simpler and more portable than dialect-specific upserts.
- **Endpoints are synchronous** because the database layer is synchronous. FastAPI runs them
  in a worker thread, which avoids blocking the event loop on database I/O.
- **Shortcuts taken,** reasonable for an exercise but worth flagging for production:
  - Tables are created at startup instead of through Alembic migrations.
  - Cache entries never expire; the transformer is assumed to be pure and stable. A real
    deployment would version the cache key with the transformer's version.
  - There is no authentication, rate limiting, or pagination over stored payloads.
- **Two ambiguities in the specification** were resolved as follows:
  - `-h` cannot mean both `--host` and `--help`; it is left as `--help` (the convention
    every CLI user expects), so `--host` has no short form.
  - The spec only defines a confirmation for `POST`. Since an identical request creates
    nothing, it returns `200` rather than `201`, and the body carries a `reused` flag.
