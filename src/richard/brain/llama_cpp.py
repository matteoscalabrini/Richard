from __future__ import annotations

import json
from collections.abc import Iterator

import httpx

from richard.brain.completion import Completion, StreamEvent, ToolCall
from richard.conversation import Message
from richard.errors import BrainUnreachable


class LlamaCppBrain:
    """OpenAI-compatible chat client for a self-hosted llama.cpp server."""

    def __init__(
        self,
        endpoint: str,
        model: str,
        api_key: str | None = None,
        client: httpx.Client | None = None,
        timeout: float = 180.0,
    ) -> None:
        base = endpoint.rstrip("/")
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        self._url = base + "/v1/chat/completions"
        self._model = model
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=timeout)

    def complete(self, messages: list[dict], tools: list[dict] | None = None) -> Completion:
        # cache_prompt: llama.cpp reuses the prompt KV cache across turns — the system
        # prompt + history prefix is identical every turn, so this cuts TTFT sharply.
        payload: dict = {"model": self._model, "messages": messages, "cache_prompt": True}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            response = self._client.post(self._url, json=payload, headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise BrainUnreachable(str(exc)) from exc
        data = response.json()
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise BrainUnreachable(f"Unexpected response shape: {data!r}") from exc
        tool_calls = []
        for raw in message.get("tool_calls") or []:
            fn = raw.get("function", {})
            try:
                arguments = json.loads(fn.get("arguments") or "{}")
            except (ValueError, TypeError):
                arguments = {}
            tool_calls.append(
                ToolCall(id=raw.get("id", ""), name=fn.get("name", ""), arguments=arguments)
            )
        return Completion(content=message.get("content"), tool_calls=tool_calls)

    def stream(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> Iterator[StreamEvent]:
        payload: dict = {"model": self._model, "messages": messages, "stream": True, "cache_prompt": True}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        acc: dict[int, dict] = {}
        try:
            with self._client.stream("POST", self._url, json=payload, headers=headers) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        delta = json.loads(data)["choices"][0]["delta"]
                    except (ValueError, KeyError, IndexError, TypeError):
                        continue
                    content = delta.get("content")
                    if content:
                        yield StreamEvent(delta=content)
                    for tc in delta.get("tool_calls") or []:
                        slot = acc.setdefault(
                            tc.get("index", 0), {"id": "", "name": "", "arguments": ""}
                        )
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"]
        except httpx.HTTPError as exc:
            raise BrainUnreachable(str(exc)) from exc
        tool_calls = []
        for _, slot in sorted(acc.items()):
            try:
                arguments = json.loads(slot["arguments"] or "{}")
            except (ValueError, TypeError):
                arguments = {}
            tool_calls.append(ToolCall(id=slot["id"], name=slot["name"], arguments=arguments))
        # Always emit exactly one terminal event per non-error stream, whether or not
        # [DONE] was received, so callers can rely on a single done=True to close the turn.
        yield StreamEvent(tool_calls=tool_calls, done=True)

    def chat(self, messages: list[Message]) -> str:
        dicts = [{"role": m.role, "content": m.content} for m in messages]
        return self.complete(dicts).content or ""
