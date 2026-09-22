"""``cache-cli``: a small client for exercising the service programmatically.

Each iteration creates a payload and reads it back, and the report records the
identifier and timing of every iteration so that caching behaviour is visible
from the command line.
"""

import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
from pydantic import AnyHttpUrl, BaseModel, Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from cache_service.schemas import PayloadCreateRequest

# Conventional placeholder for "read from stdin" / "write to stdout".
STREAM = "-"

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2


class CLISettings(BaseSettings):
    """Create a payload on a cache service instance and read it back.

    Exactly one of --input or --json supplies the request body. Note that -h is
    reserved for --help, so --host has no short form.
    """

    model_config = SettingsConfigDict(
        cli_parse_args=True,
        cli_prog_name="cache-cli",
        cli_kebab_case=True,
        cli_shortcuts={"repeat": "r", "input": "i", "json": "j", "output": "o"},
        env_prefix="CACHE_CLI_",
        populate_by_name=True,
    )

    host: AnyHttpUrl = Field(
        default=AnyHttpUrl("http://localhost:8000"), description="Base URL of the service"
    )
    repeat: int = Field(default=1, ge=1, description="Number of create/read iterations")
    input: str | None = Field(default=None, description='Input file, or "-" for stdin')
    json_payload: str | None = Field(
        default=None, alias="json", description="Inline JSON request body"
    )
    output: str = Field(default=STREAM, description='Output file, or "-" for stdout')
    timeout: float = Field(default=30.0, gt=0, description="HTTP timeout in seconds")

    @field_validator("input")
    @classmethod
    def _input_must_be_readable(cls, value: str | None) -> str | None:
        if value is not None and value != STREAM and not Path(value).is_file():
            raise ValueError(f"input file does not exist: {value}")
        return value

    @model_validator(mode="after")
    def _needs_exactly_one_input_source(self) -> "CLISettings":
        if (self.input is None) == (self.json_payload is None):
            raise ValueError("provide exactly one of --input or --json")
        return self

    def read_request(self) -> PayloadCreateRequest:
        """Parse and validate the request body before any HTTP call is made."""
        raw = self.json_payload if self.json_payload is not None else self._read_input()
        return PayloadCreateRequest.model_validate_json(raw)

    def write_output(self, content: str) -> None:
        text = content if content.endswith("\n") else f"{content}\n"
        if self.output == STREAM:
            sys.stdout.write(text)
        else:
            Path(self.output).write_text(text, encoding="utf-8")

    def _read_input(self) -> str:
        assert self.input is not None  # guaranteed by _needs_exactly_one_input_source
        if self.input == STREAM:
            return sys.stdin.read()
        return Path(self.input).read_text(encoding="utf-8")


class IterationReport(BaseModel):
    payload_id: str
    reused: bool = Field(description="True when the service returned an existing identifier")
    elapsed_seconds: float


class RunReport(BaseModel):
    host: str
    iterations: list[IterationReport]
    output: str


def run(settings: CLISettings, client: httpx.Client | None = None) -> RunReport:
    """Run the configured iterations and return their combined report."""
    request = settings.read_request()
    with _http_client(settings, client) as http:
        results = [_create_and_read(http, request) for _ in range(settings.repeat)]

    # Every iteration sends the same request, so the last output stands for all of them.
    iterations, outputs = zip(*results, strict=True)
    return RunReport(host=str(settings.host), iterations=list(iterations), output=outputs[-1])


def main() -> None:
    raise SystemExit(_run_from_command_line())


def _create_and_read(
    http: httpx.Client, request: PayloadCreateRequest
) -> tuple[IterationReport, str]:
    started = time.perf_counter()

    created = http.post("/payload", json=request.model_dump())
    created.raise_for_status()
    identifier = created.json()

    read = http.get(f"/payload/{identifier['id']}")
    read.raise_for_status()

    report = IterationReport(
        payload_id=identifier["id"],
        reused=identifier["reused"],
        elapsed_seconds=round(time.perf_counter() - started, 6),
    )
    return report, read.json()["output"]


@contextmanager
def _http_client(settings: CLISettings, client: httpx.Client | None) -> Iterator[httpx.Client]:
    if client is not None:
        # An injected client belongs to the caller, which also closes it.
        yield client
        return
    with httpx.Client(base_url=str(settings.host), timeout=settings.timeout) as owned:
        yield owned


def _run_from_command_line() -> int:
    try:
        settings = CLISettings()
    except ValidationError as error:
        return _fail(_describe(error), EXIT_USAGE)

    try:
        report = run(settings)
    except ValidationError as error:
        # Covers malformed JSON as well: Pydantic reports it as a validation error.
        return _fail(f"invalid request body: {_describe(error)}", EXIT_USAGE)
    except OSError as error:
        return _fail(f"cannot read input: {error}", EXIT_USAGE)
    except httpx.HTTPStatusError as error:
        return _fail(f"server returned {error.response.status_code}: {error.response.text}")
    except httpx.HTTPError as error:
        return _fail(f"request to {settings.host} failed: {error}")

    try:
        settings.write_output(report.model_dump_json(indent=2))
    except OSError as error:
        return _fail(f"cannot write output: {error}")
    return EXIT_OK


def _describe(error: ValidationError) -> str:
    return "; ".join(detail["msg"].removeprefix("Value error, ") for detail in error.errors())


def _fail(message: str, code: int = EXIT_FAILURE) -> int:
    print(f"cache-cli: {message}", file=sys.stderr)
    return code


if __name__ == "__main__":
    main()
