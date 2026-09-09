from __future__ import annotations

import json
from collections.abc import Iterator

import httpx

from richard.brain.completion import Completion, StreamEvent, ToolCall
from richard.conversation import Message
from richard.errors import BrainRejectedInput, BrainUnreachable


def _parse_arguments(raw: str) -> dict:
    """Tool arguments off the wire. Anything but a JSON object becomes {} —
    small models sometimes emit a bare string or list, and providers index
    into arguments with .get()."""
    try:
        arguments = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}
    return arguments if isinstance(arguments, dict) else {}


def _has_images(messages: list[dict]) -> bool:
    for message in messages:
        content = message.get("content")
        if isinstance(content, list) and any(
            isinstance(p, dict) and p.get("type") == "image_url" for p in content
        ):
            return True
    return False


def _check_status(response: httpx.Response, messages: list[dict]) -> None:
    """raise_for_status, except that a 4xx on a request with images is a rejected
    input (the model cannot see), not an unreachable brain."""
    if 400 <= response.status_code < 500 and _has_images(messages):
        try:
            body = response.read()[:500].decode(errors="replace")
        except Exception:  # body unreadable; the status is the point
            body = ""
        raise BrainRejectedInput(f"brain rejected the request ({response.status_code}): {body}")
    response.raise_for_status()


class LlamaCppBrain:
    """OpenAI-compatible chat client for a self-hosted llama.cpp server."""

    def __init__(
        self,
        endpoint: str,
        model: str,
        api_key: str | None = None,
        client: httpx.Client | None = None,
        timeout: float = 180.0,
        extra_body: dict | None = None,
    ) -> None:
        base = endpoint.rstrip("/")
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        self._url = base + "/v1/chat/completions"
        self._model = model
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=timeout)
        # Server-specific knobs merged into every request body verbatim (e.g. the thinking
        # controls a llama.cpp/SGLang chat template accepts). Required keys always win.
        self._extra_body = dict(extra_body or {})

    def complete(self, messages: list[dict], tools: list[dict] | None = None) -> Completion:
        # cache_prompt: llama.cpp reuses the prompt KV cache across turns — the system
        # prompt + history prefix is identical every turn, so this cuts TTFT sharply.
        payload: dict = {**self._extra_body, "model": self._model, "messages": messages, "cache_prompt": True}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            response = self._client.post(self._url, json=payload, headers=headers)
            _check_status(response, messages)
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
            tool_calls.append(
                ToolCall(
                    id=raw.get("id", ""),
                    name=fn.get("name", ""),
                    arguments=_parse_arguments(fn.get("arguments")),
                )
            )
        return Completion(content=message.get("content"), tool_calls=tool_calls)

    def stream(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> Iterator[StreamEvent]:
        payload: dict = {**self._extra_body, "model": self._model, "messages": messages, "stream": True, "cache_prompt": True}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        acc: dict[int, dict] = {}
        try:
            with self._client.stream("POST", self._url, json=payload, headers=headers) as response:
                _check_status(response, messages)
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
            tool_calls.append(
                ToolCall(id=slot["id"], name=slot["name"],
                         arguments=_parse_arguments(slot["arguments"]))
            )
        # Always emit exactly one terminal event per non-error stream, whether or not
        # [DONE] was received, so callers can rely on a single done=True to close the turn.
        yield StreamEvent(tool_calls=tool_calls, done=True)

    def chat(self, messages: list[Message]) -> str:
        dicts = [{"role": m.role, "content": m.content} for m in messages]
        return self.complete(dicts).content or ""
