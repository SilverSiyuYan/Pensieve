"""OpenAI-compatible DashScope service for memory answers and intent parsing."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI


load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

SYSTEM_PROMPT = """你是一个记忆助手。用户会询问他们之前记录的事情。
请根据提供的记忆条目，用自然语言整合回答用户的问题。
要求：1）回答要流畅自然，像朋友提醒一样 2）在回答末尾，
用【原始记录】标注列出每条相关记忆的原文和时间。"""

INTENT_SYSTEM_PROMPT = """你是一个用户意图分类器。判断输入是存储意图还是查询意图。
存储意图是用户希望系统记住一件事；查询意图是用户询问已有记忆。
只返回 JSON，不要使用 Markdown，格式为：
{"intent":"store"或"query","extracted_content":"存储的核心内容；查询时为空字符串","extracted_tags":["标签"]}。
提取简洁、有意义的时间、主题或类别标签。"""

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    """Create the OpenAI SDK client using the local DashScope-compatible config."""
    global _client
    if _client is None:
        api_key = os.environ["OPENAI_API_KEY"]
        base_url = os.environ["OPENAI_BASE_URL"]
        masked_key = f"{api_key[:7]}...{api_key[-4:]}" if len(api_key) > 11 else "***"
        print("调试：当前读取到的 API Key 是:", masked_key)
        print("调试：当前读取到的 Base URL 是:", base_url)
        _client = OpenAI(
            api_key=api_key,
            base_url=base_url,
        )
    return _client


def _model_name() -> str:
    return os.getenv("MODEL_NAME", "qwen-plus")


def _memory_prompt(query: str, retrieved_memories: list[dict[str, Any]]) -> str:
    lines = [f"用户问题：{query}", "相关记忆条目："]
    for index, memory in enumerate(retrieved_memories, start=1):
        lines.append(
            f"{index}. [{memory.get('created_at', '')}] {memory.get('content', '')}"
            f"（标签：{memory.get('tags', '')}）"
        )
    return "\n".join(lines)


def generate_integrated_answer(
    query: str, retrieved_memories: list[dict[str, Any]]
) -> str:
    """Ask the LLM to combine semantic-search results into a natural answer."""
    response = _get_client().chat.completions.create(
        model=_model_name(),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _memory_prompt(query, retrieved_memories)},
        ],
        temperature=0.4,
    )
    return response.choices[0].message.content or ""


def _parse_json_response(content: str) -> dict[str, Any]:
    """Accept JSON returned directly or inside an accidental Markdown fence."""
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    payload = json.loads(cleaned)
    if not isinstance(payload, dict):
        raise ValueError("Intent response must be a JSON object")
    return payload


def classify_intent(query: str) -> dict[str, Any]:
    """Classify a message as a memory store request or a memory query."""
    response = _get_client().chat.completions.create(
        model=_model_name(),
        messages=[
            {"role": "system", "content": INTENT_SYSTEM_PROMPT},
            {"role": "user", "content": f"用户输入：{query}"},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    payload = _parse_json_response(response.choices[0].message.content or "{}")
    intent = "store" if payload.get("intent") == "store" else "query"
    tags = payload.get("extracted_tags", [])
    return {
        "intent": intent,
        "extracted_content": str(payload.get("extracted_content", "")),
        "extracted_tags": [str(tag) for tag in tags] if isinstance(tags, list) else [],
    }
