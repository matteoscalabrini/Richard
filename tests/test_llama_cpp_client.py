import json

import httpx
import pytest

from richard.brain.llama_cpp import LlamaCppBrain
from richard.conversation import Message
from richard.errors import BrainUnreachable


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_chat_returns_assistant_content():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "hello there"}}]},
        )

    brain = LlamaCppBrain("http://box:8080", "local", client=_client(handler))
    assert brain.chat([Message("user", "hi")]) == "hello there"


def test_chat_sends_model_and_messages():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    brain = LlamaCppBrain("http://box:8080", "my-model", client=_client(handler))
    brain.chat([Message("system", "s"), Message("user", "hi")])
    assert captured["model"] == "my-model"
    assert captured["messages"] == [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "hi"},
    ]


def test_chat_raises_brain_unreachable_on_transport_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    brain = LlamaCppBrain("http://box:8080", "local", client=_client(handler))
    with pytest.raises(BrainUnreachable):
        brain.chat([Message("user", "hi")])


def test_chat_raises_brain_unreachable_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    brain = LlamaCppBrain("http://box:8080", "local", client=_client(handler))
    with pytest.raises(BrainUnreachable):
        brain.chat([Message("user", "hi")])


def test_chat_raises_brain_unreachable_on_malformed_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "model not loaded"})

    brain = LlamaCppBrain("http://box:8080", "local", client=_client(handler))
    with pytest.raises(BrainUnreachable):
        brain.chat([Message("user", "hi")])


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://box:8080",
        "http://box:8080/",
        "http://box:8080/v1",
        "http://box:8080/v1/",
    ],
)
def test_chat_normalizes_endpoint_to_single_v1(endpoint):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    brain = LlamaCppBrain(endpoint, "m", client=_client(handler))
    brain.chat([Message("user", "hi")])
    assert captured["url"] == "http://box:8080/v1/chat/completions"


def test_default_client_uses_timeout():
    brain = LlamaCppBrain("http://box:8080", "m", timeout=7.0)
    assert brain._client.timeout.read == 7.0


def test_complete_parses_tool_calls():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "abc",
                                    "type": "function",
                                    "function": {
                                        "name": "remember",
                                        "arguments": '{"text": "likes tea"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    brain = LlamaCppBrain("http://box:8080", "m", client=_client(handler))
    completion = brain.complete([{"role": "user", "content": "remember tea"}], tools=[{"x": 1}])
    assert completion.content == ""
    assert len(completion.tool_calls) == 1
    call = completion.tool_calls[0]
    assert (call.id, call.name, call.arguments) == ("abc", "remember", {"text": "likes tea"})


def test_complete_coerces_non_object_tool_arguments_to_empty_dict():
    # Small models sometimes emit arguments that are valid JSON but not an
    # object (a bare string or list). Providers index into arguments with
    # .get(), so anything except a dict must be coerced.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "abc",
                                    "type": "function",
                                    "function": {
                                        "name": "call_home_assistant_service",
                                        "arguments": '"volume_off"',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    brain = LlamaCppBrain("http://box:8080", "m", client=_client(handler))
    completion = brain.complete([{"role": "user", "content": "mute the bose"}])
    assert completion.tool_calls[0].arguments == {}


def test_complete_without_tool_calls():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    brain = LlamaCppBrain("http://box:8080", "m", client=_client(handler))
    completion = brain.complete([{"role": "user", "content": "hi"}])
    assert completion.content == "hi"
    assert completion.tool_calls == []


def test_complete_omits_tools_when_none():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    brain = LlamaCppBrain("http://box:8080", "m", client=_client(handler))
    brain.complete([{"role": "user", "content": "hi"}])
    assert "tools" not in captured


def test_complete_requests_prompt_cache():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    brain = LlamaCppBrain("http://box:8080", "local", client=_client(handler))
    brain.complete([{"role": "user", "content": "hi"}])
    assert captured["cache_prompt"] is True


def test_stream_requests_prompt_cache():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',
            headers={"content-type": "text/event-stream"},
        )

    brain = LlamaCppBrain("http://box:8080", "local", client=_client(handler))
    list(brain.stream([{"role": "user", "content": "hi"}]))
    assert captured["cache_prompt"] is True


from richard.conversation import user_parts  # noqa: E402
from richard.errors import BrainRejectedInput  # noqa: E402

_IMG = "data:image/jpeg;base64,/9j/4AAQ"


def test_complete_sends_content_parts_unchanged():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "a mug"}}]})

    brain = LlamaCppBrain("http://box:8080", "m", client=_client(handler))
    parts = user_parts("what is this?", [_IMG])
    brain.complete([{"role": "system", "content": "s"}, {"role": "user", "content": parts}])
    assert captured["messages"][1] == {"role": "user", "content": parts}


def test_complete_4xx_with_images_raises_brain_rejected_input():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text='{"error": "image input not supported"}')

    brain = LlamaCppBrain("http://box:8080", "m", client=_client(handler))
    with pytest.raises(BrainRejectedInput, match="400"):
        brain.complete([{"role": "user", "content": user_parts("look", [_IMG])}])


def test_complete_4xx_without_images_stays_brain_unreachable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad")

    brain = LlamaCppBrain("http://box:8080", "m", client=_client(handler))
    with pytest.raises(BrainUnreachable) as excinfo:
        brain.complete([{"role": "user", "content": "hi"}])
    assert not isinstance(excinfo.value, BrainRejectedInput)


def test_stream_4xx_with_images_raises_brain_rejected_input():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, text="unprocessable")

    brain = LlamaCppBrain("http://box:8080", "m", client=_client(handler))
    with pytest.raises(BrainRejectedInput, match="422"):
        list(brain.stream([{"role": "user", "content": user_parts(None, [_IMG])}]))


def test_stream_passes_parts_and_yields_deltas():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        body = 'data: {"choices": [{"delta": {"content": "a mug"}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, text=body)

    brain = LlamaCppBrain("http://box:8080", "m", client=_client(handler))
    parts = user_parts("look", [_IMG])
    events = list(brain.stream([{"role": "user", "content": parts}]))
    assert captured["messages"] == [{"role": "user", "content": parts}]
    assert events[0].delta == "a mug" and events[-1].done
