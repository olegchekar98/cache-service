import io
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from cache_service.cli import (
    EXIT_FAILURE,
    EXIT_OK,
    EXIT_USAGE,
    CLISettings,
    main,
    run,
    run_from_command_line,
)


def settings_from(argv: list[str]) -> CLISettings:
    """Build settings exactly like the entry point does, from a command line."""
    return CLISettings(_cli_parse_args=argv)  # type: ignore[call-arg]  # pydantic-settings hook


def test_parses_short_flags(tmp_path: Path) -> None:
    input_file = tmp_path / "request.json"
    input_file.write_text("{}", encoding="utf-8")

    settings = settings_from(
        ["--host", "http://example.test:9000", "-r", "3", "-i", str(input_file), "-o", "out.json"]
    )

    assert str(settings.host) == "http://example.test:9000/"
    assert settings.repeat == 3
    assert settings.input == str(input_file)
    assert settings.output == "out.json"


def test_requires_exactly_one_input_source() -> None:
    with pytest.raises(ValidationError, match="exactly one of --input or --json"):
        settings_from([])

    with pytest.raises(ValidationError, match="exactly one of --input or --json"):
        settings_from(["-j", "{}", "-i", "-"])


def test_rejects_a_missing_input_file() -> None:
    with pytest.raises(ValidationError, match="input file does not exist"):
        settings_from(["-i", "no-such-file.json"])


def test_rejects_a_repeat_below_one() -> None:
    with pytest.raises(ValidationError):
        settings_from(["-j", "{}", "-r", "0"])


def test_reads_the_request_from_a_file(
    tmp_path: Path, sample_request: dict[str, list[str]]
) -> None:
    input_file = tmp_path / "request.json"
    input_file.write_text(json.dumps(sample_request), encoding="utf-8")

    request = settings_from(["-i", str(input_file)]).read_request()

    assert request.list_1 == sample_request["list_1"]


def test_reads_the_request_from_stdin(
    monkeypatch: pytest.MonkeyPatch, sample_request: dict[str, list[str]]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(sample_request)))

    request = settings_from(["-i", "-"]).read_request()

    assert request.list_2 == sample_request["list_2"]


def test_rejects_an_invalid_request_body() -> None:
    settings = settings_from(["-j", '{"list_1": ["a"], "list_2": ["b", "c"]}'])

    with pytest.raises(ValidationError, match="same length"):
        settings.read_request()


def test_writes_the_report_to_stdout_by_default(capsys: pytest.CaptureFixture[str]) -> None:
    settings_from(["-j", "{}"]).write_output('{"output": "A"}')

    assert capsys.readouterr().out == '{"output": "A"}\n'


def test_writes_the_report_to_a_file(tmp_path: Path) -> None:
    output_file = tmp_path / "report.json"

    settings_from(["-j", "{}", "-o", str(output_file)]).write_output('{"output": "A"}')

    assert output_file.read_text(encoding="utf-8") == '{"output": "A"}\n'


def test_run_creates_and_reads_a_payload(
    client: TestClient, sample_request: dict[str, list[str]], sample_output: str
) -> None:
    settings = settings_from(["-j", json.dumps(sample_request)])

    report = run(settings, client=client)

    assert report.output == sample_output
    assert len(report.iterations) == 1
    assert report.iterations[0].reused is False


def test_repeated_runs_report_the_reused_identifier(
    client: TestClient, sample_request: dict[str, list[str]]
) -> None:
    settings = settings_from(["-j", json.dumps(sample_request), "-r", "3"])

    report = run(settings, client=client)

    assert [iteration.reused for iteration in report.iterations] == [False, True, True]
    assert len({iteration.payload_id for iteration in report.iterations}) == 1


def test_main_reports_usage_errors_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["cache-cli"])

    with pytest.raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == EXIT_USAGE
    assert "exactly one of --input or --json" in capsys.readouterr().err


def test_writes_the_report_on_success(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    tmp_path: Path,
    sample_request: dict[str, list[str]],
    sample_output: str,
) -> None:
    report_file = tmp_path / "report.json"
    monkeypatch.setattr(
        "sys.argv", ["cache-cli", "-j", json.dumps(sample_request), "-o", str(report_file)]
    )

    assert run_from_command_line(client) == EXIT_OK
    assert json.loads(report_file.read_text(encoding="utf-8"))["output"] == sample_output


def test_reports_an_error_response_from_the_server(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    sample_request: dict[str, list[str]],
) -> None:
    unavailable = httpx.MockTransport(lambda _: httpx.Response(503, text="unavailable"))
    monkeypatch.setattr("sys.argv", ["cache-cli", "-j", json.dumps(sample_request)])

    with httpx.Client(transport=unavailable, base_url="http://cache.test") as client:
        assert run_from_command_line(client) == EXIT_FAILURE

    assert "server returned 503" in capsys.readouterr().err


def test_reports_an_unreachable_server(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    sample_request: dict[str, list[str]],
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr("sys.argv", ["cache-cli", "-j", json.dumps(sample_request)])

    with httpx.Client(
        transport=httpx.MockTransport(refuse), base_url="http://cache.test"
    ) as client:
        assert run_from_command_line(client) == EXIT_FAILURE

    assert "connection refused" in capsys.readouterr().err
