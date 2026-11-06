"""Provider-agnostic LLM interface. One method: structured JSON out.

Fork makes a single call per check (reading proposal) and the eval makes one
more per fork (plausibility judge). Nothing else talks to a model.
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol


class LLM(Protocol):
    name: str

    def json(self, system: str, user: str, schema: dict, max_tokens: int = 4096) -> dict: ...


class NullLLM:
    """No model: heuristics only."""

    name = "none"

    def json(self, system: str, user: str, schema: dict, max_tokens: int = 4096) -> dict:
        raise RuntimeError("NullLLM cannot answer")


class AnthropicLLM:
    name = "anthropic"

    def __init__(self, model: str | None = None, effort: str = "medium", client: Any = None):
        import anthropic

        self.model = model or os.environ.get("FORK_MODEL") or "claude-sonnet-5-5"
        self.effort = effort
        self.client = client or anthropic.Anthropic()

    def json(self, system: str, user: str, schema: dict, max_tokens: int = 4096) -> dict:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError(f"model refused: {getattr(resp, 'stop_details', None)}")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return json.loads(text)


class OpenAICompatibleLLM:
    """Any /v1/chat/completions endpoint that supports json_schema response_format."""

    name = "openai"

    def __init__(self, model: str | None = None, base_url: str | None = None, api_key: str | None = None):
        import httpx

        self.model = model or os.environ.get("FORK_MODEL") or "gpt-5"
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.http = httpx.Client(timeout=120)

    def json(self, system: str, user: str, schema: dict, max_tokens: int = 4096) -> dict:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "out", "schema": schema, "strict": True}},
            "max_completion_tokens": max_tokens,
        }
        r = self.http.post(f"{self.base_url}/chat/completions", json=body, headers={"Authorization": f"Bearer {self.api_key}"})
        r.raise_for_status()
        return json.loads(r.json()["choices"][0]["message"]["content"])


class FakeLLM:
    """For tests: returns canned answers in order, records prompts."""

    name = "fake"

    def __init__(self, answers: list[dict]):
        self.answers = list(answers)
        self.prompts: list[tuple[str, str]] = []

    def json(self, system: str, user: str, schema: dict, max_tokens: int = 4096) -> dict:
        self.prompts.append((system, user))
        if not self.answers:
            raise RuntimeError("FakeLLM exhausted")
        return self.answers.pop(0)


def default_llm(provider: str | None = None, model: str | None = None) -> LLM:
    provider = (provider or os.environ.get("FORK_LLM_PROVIDER") or "").lower()
    if provider == "none":
        return NullLLM()
    if provider == "openai":
        return OpenAICompatibleLLM(model=model)
    if provider == "anthropic" or os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicLLM(model=model)
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAICompatibleLLM(model=model)
    return NullLLM()
