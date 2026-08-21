"""Prompt construction tests; all OpenAI-compatible calls are mocked."""

from types import SimpleNamespace
from typing import Any
import os

import llm_service


class MockCompletions:
    def __init__(self, response_content: str) -> None:
        self.response_content = response_content
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.response_content))]
        )


def mock_client(response_content: str) -> tuple[SimpleNamespace, MockCompletions]:
    completions = MockCompletions(response_content)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def test_generate_integrated_answer_builds_required_prompts(monkeypatch) -> None:
    client, completions = mock_client("周四记得洗头。\n【原始记录】[2026-08-21] 周四洗头")
    monkeypatch.setattr(llm_service, "_client", client)

    answer = llm_service.generate_integrated_answer(
        "我这周有什么个人护理安排？",
        [
            {"content": "周四洗头", "tags": "周四,个人护理", "created_at": "2026-08-21"},
            {"content": "周五游泳", "tags": "周五,运动", "created_at": "2026-08-22"},
        ],
    )

    call = completions.calls[0]
    assert answer.startswith("周四记得洗头")
    assert call["model"] == "qwen-plus"
    assert call["messages"][0]["content"] == llm_service.SYSTEM_PROMPT
    assert "用户问题：我这周有什么个人护理安排？" in call["messages"][1]["content"]
    assert "1. [2026-08-21] 周四洗头（标签：周四,个人护理）" in call["messages"][1]["content"]
    assert "2. [2026-08-22] 周五游泳（标签：周五,运动）" in call["messages"][1]["content"]


def test_classify_intent_parses_store_json_and_builds_prompt(monkeypatch) -> None:
    client, completions = mock_client(
        '{"intent":"store","extracted_content":"下周三交报告","extracted_tags":["下周三","工作"]}'
    )
    monkeypatch.setattr(llm_service, "_client", client)

    result = llm_service.classify_intent("帮我记住下周三要交报告")

    call = completions.calls[0]
    assert result == {
        "intent": "store",
        "extracted_content": "下周三交报告",
        "extracted_tags": ["下周三", "工作"],
    }
    assert call["messages"][0]["content"] == llm_service.INTENT_SYSTEM_PROMPT
    assert call["messages"][1]["content"] == "用户输入：帮我记住下周三要交报告"
    assert call["response_format"] == {"type": "json_object"}


def test_classify_intent_parses_query_json(monkeypatch) -> None:
    client, _ = mock_client(
        '{"intent":"query","extracted_content":"","extracted_tags":[]}'
    )
    monkeypatch.setattr(llm_service, "_client", client)

    result = llm_service.classify_intent("这周我有什么安排？")

    assert result == {
        "intent": "query",
        "extracted_content": "",
        "extracted_tags": [],
    }


def test_dotenv_configuration_matches_client_settings(monkeypatch) -> None:
    """The local dotenv configuration feeds the same values used by OpenAI SDK."""
    monkeypatch.setattr(llm_service, "_client", None)

    client = llm_service._get_client()

    assert os.environ["OPENAI_API_KEY"]
    assert os.environ["OPENAI_BASE_URL"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert llm_service._model_name() == "qwen-plus"
    assert client.api_key == os.environ["OPENAI_API_KEY"]
    assert str(client.base_url).rstrip("/") == os.environ["OPENAI_BASE_URL"].rstrip("/")
