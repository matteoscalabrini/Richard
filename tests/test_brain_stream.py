import json

import httpx
import pytest

from richard.brain.completion import StreamEvent
from richard.brain.llama_cpp import LlamaCppBrain
from richard.errors import BrainUnreachable


def _sse(*chunks: dict) -> bytes:
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return body.encode()


def _brain(handler) -> LlamaCppBrain:
    return LlamaCppBrain(
        "http://x", "m", client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_stream_yields_content_deltas_then_done():
    def handler(request):
        return httpx.Response(
            200,
            content=_sse(
                {"choices": [{"delta": {"content": "Fan's "}}]},
                {"choices": [{"delta": {"content": "off."}}]},
            ),
        )

    events = list(_brain(handler).stream([{"role": "user", "content": "off"}]))
    assert [e.delta for e in events if e.delta] == ["Fan's ", "off."]
    assert events[-1].done is True
    assert events[-1].tool_calls == []


def test_stream_assembles_fragmented_tool_calls():
    def handler(request):
        return httpx.Response(
            200,
            content=_sse(
                {"choices": [{"delta": {"tool_calls": [
                    {"index": 0, "id": "c1", "function": {"name": "set_fan", "arguments": ""}}]}}]},
                {"choices": [{"delta": {"tool_calls": [
                    {"index": 0, "function": {"arguments": "{\"on\""}}]}}]},
                {"choices": [{"delta": {"tool_calls": [
                    {"index": 0, "function": {"arguments": ": false}"}}]}}]},
            ),
        )

    events = list(_brain(handler).stream([{"role": "user", "content": "off"}]))
    assert events[-1].done is True
    calls = events[-1].tool_calls
    assert len(calls) == 1
    assert calls[0].id == "c1"
    assert calls[0].name == "set_fan"
    assert calls[0].arguments == {"on": False}


def test_stream_coerces_non_object_tool_arguments_to_empty_dict():
    def handler(request):
        return httpx.Response(
            200,
            content=_sse(
                {"choices": [{"delta": {"tool_calls": [
                    {"index": 0, "id": "c1",
                     "function": {"name": "call_home_assistant_service", "arguments": '"volu'}}]}}]},
                {"choices": [{"delta": {"tool_calls": [
                    {"index": 0, "function": {"arguments": 'me_off"'}}]}}]},
            ),
        )

    events = list(_brain(handler).stream([{"role": "user", "content": "mute the bose"}]))
    assert events[-1].tool_calls[0].arguments == {}


def test_stream_wraps_transport_error():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(BrainUnreachable):
        list(_brain(handler).stream([{"role": "user", "content": "hi"}]))


def test_stream_wraps_http_error_status():
    def handler(request):
        return httpx.Response(503, text="Service Unavailable")

    with pytest.raises(BrainUnreachable):
        list(_brain(handler).stream([{"role": "user", "content": "hi"}]))
