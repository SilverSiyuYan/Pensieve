"""End-to-end authentication and cross-user isolation tests."""

from pathlib import Path

from fastapi.testclient import TestClient
import pytest

import database
import main
import vector_store


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "memory.db")
    monkeypatch.setattr(vector_store, "CHROMA_PATH", str(tmp_path / "chroma_data"))
    monkeypatch.setattr(
        main,
        "generate_integrated_answer",
        lambda query, memories: "；".join(item["content"] for item in memories) or "没有找到记忆",
    )
    with TestClient(main.app) as test_client:
        yield test_client


def register(client: TestClient, email: str) -> str:
    response = client.post("/api/auth/register", json={"email": email, "password": "safe-password-123"})
    assert response.status_code == 201
    return response.json()["access_token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_memory_routes_require_authentication(client: TestClient) -> None:
    assert client.get("/api/memories").status_code == 401
    assert client.post("/api/memory/store", json={"content": "secret"}).status_code == 401


def test_user_b_cannot_retrieve_or_delete_user_a_memory(client: TestClient) -> None:
    token_a = register(client, "a@example.com")
    token_b = register(client, "b@example.com")

    stored = client.post(
        "/api/memory/store",
        headers=auth(token_a),
        json={"content": "我周五游泳", "tags": ["周五", "运动"], "category": "日程"},
    )
    assert stored.status_code == 200
    memory_id = stored.json()["memory_id"]

    assert [item["content"] for item in client.get("/api/memories", headers=auth(token_a)).json()] == ["我周五游泳"]
    assert client.get("/api/memories", headers=auth(token_b)).json() == []

    queried = client.post(
        "/api/memory/query", headers=auth(token_b), json={"query": "我周五做什么"}
    )
    assert queried.status_code == 200
    assert queried.json()["source_memories"] == []
    assert "游泳" not in queried.json()["answer"]

    assert client.delete(f"/api/memory/{memory_id}", headers=auth(token_b)).status_code == 404
    assert client.delete(f"/api/memory/{memory_id}", headers=auth(token_a)).status_code == 200


def test_logout_revokes_session(client: TestClient) -> None:
    token = register(client, "logout@example.com")
    assert client.get("/api/auth/me", headers=auth(token)).status_code == 200
    assert client.post("/api/auth/logout", headers=auth(token)).status_code == 200
    assert client.get("/api/auth/me", headers=auth(token)).status_code == 401


def test_conversation_history_is_isolated(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        main,
        "classify_intent",
        lambda value: {"intent": "store", "extracted_content": value, "extracted_tags": []},
    )
    token_a = register(client, "conversation-a@example.com")
    token_b = register(client, "conversation-b@example.com")

    response = client.post(
        "/api/memory/auto", headers=auth(token_a), json={"input": "我的私人对话"}
    )
    conversation_id = response.json()["conversation_id"]

    assert len(client.get(f"/api/conversations/{conversation_id}/messages", headers=auth(token_a)).json()) == 2
    assert client.get(f"/api/conversations/{conversation_id}/messages", headers=auth(token_b)).status_code == 404
    assert client.get("/api/conversations", headers=auth(token_b)).json() == []
