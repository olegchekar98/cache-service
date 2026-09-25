from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine


def test_create_app_uses_the_engine_it_was_given(app: FastAPI, engine: Engine) -> None:
    assert app.state.engine is engine


def test_health(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_then_read_returns_the_interleaved_payload(
    client: TestClient, sample_request: dict[str, list[str]], sample_output: str
) -> None:
    created = client.post("/payload", json=sample_request)

    assert created.status_code == 201
    assert created.json()["reused"] is False

    read = client.get(f"/payload/{created.json()['id']}")

    assert read.status_code == 200
    assert read.json() == {"output": sample_output}


def test_identical_request_reuses_the_identifier(
    client: TestClient, sample_request: dict[str, list[str]], transformer_calls: list[str]
) -> None:
    first = client.post("/payload", json=sample_request)
    transformer_calls.clear()

    second = client.post("/payload", json=sample_request)

    assert second.status_code == 200, "nothing was created, so 201 would be misleading"
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["reused"] is True
    assert transformer_calls == []


def test_overlapping_request_only_transforms_new_strings(
    client: TestClient, sample_request: dict[str, list[str]], transformer_calls: list[str]
) -> None:
    client.post("/payload", json=sample_request)
    transformer_calls.clear()

    client.post(
        "/payload",
        json={"list_1": sample_request["list_1"], "list_2": ["brand new string"] * 3},
    )

    assert transformer_calls == ["brand new string"]


def test_read_unknown_identifier_returns_404(client: TestClient) -> None:
    response = client.get("/payload/unknown-id")

    assert response.status_code == 404
    assert response.json()["detail"] == "Payload not found"


def test_rejects_lists_of_different_lengths(client: TestClient) -> None:
    response = client.post("/payload", json={"list_1": ["a"], "list_2": ["b", "c"]})

    assert response.status_code == 422
    assert "same length" in response.text


def test_rejects_empty_lists(client: TestClient) -> None:
    response = client.post("/payload", json={"list_1": [], "list_2": []})

    assert response.status_code == 422


def test_rejects_a_missing_list(client: TestClient) -> None:
    response = client.post("/payload", json={"list_1": ["a"]})

    assert response.status_code == 422
