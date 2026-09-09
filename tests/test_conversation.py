from richard.conversation import DEFAULT_SYSTEM_PROMPT, Conversation, Message


def test_messages_starts_with_system_prompt():
    convo = Conversation()
    assert convo.messages() == [Message("system", DEFAULT_SYSTEM_PROMPT)]


def test_messages_preserve_order():
    convo = Conversation(system_prompt="sys")
    convo.add_user("hi")
    convo.add_assistant("hello")
    convo.add_user("bye")
    assert convo.messages() == [
        Message("system", "sys"),
        Message("user", "hi"),
        Message("assistant", "hello"),
        Message("user", "bye"),
    ]


def test_history_excludes_system_prompt():
    convo = Conversation(system_prompt="sys")
    convo.add_user("hi")
    convo.add_assistant("yo")
    assert convo.history() == [Message("user", "hi"), Message("assistant", "yo")]


def test_tool_round_is_stored_and_rendered_as_served():
    convo = Conversation(system_prompt="sys")
    convo.add_user("remember tea")
    calls = [{"id": "1", "type": "function", "function": {"name": "remember", "arguments": '{"text": "tea"}'}}]
    convo.add_tool_call(None, calls)
    convo.add_tool_result("1", "Remembered.")
    convo.add_assistant("Noted.")
    assert [m.to_chat() for m in convo.history()] == [
        {"role": "user", "content": "remember tea"},
        {"role": "assistant", "content": None, "tool_calls": calls},
        {"role": "tool", "tool_call_id": "1", "content": "Remembered."},
        {"role": "assistant", "content": "Noted."},
    ]


def test_plain_messages_render_without_tool_fields():
    convo = Conversation(system_prompt="sys")
    convo.add_user("hi")
    convo.add_assistant("yo")
    assert [m.to_chat() for m in convo.history()] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
    ]


from richard.conversation import user_parts  # noqa: E402


IMG = "data:image/jpeg;base64,/9j/4AAQ"


def test_user_parts_builds_text_then_images():
    assert user_parts("what is this?", [IMG]) == [
        {"type": "text", "text": "what is this?"},
        {"type": "image_url", "image_url": {"url": IMG}},
    ]
    assert user_parts(None, [IMG]) == [{"type": "image_url", "image_url": {"url": IMG}}]
    assert user_parts("", [IMG]) == [{"type": "image_url", "image_url": {"url": IMG}}]


def test_parts_content_passes_through_to_chat_unchanged():
    convo = Conversation(system_prompt="sys")
    parts = user_parts("look", [IMG])
    convo.add_user(parts)
    convo.add_assistant("A mug.")
    assert [m.to_chat() for m in convo.history()] == [
        {"role": "user", "content": parts},
        {"role": "assistant", "content": "A mug."},
    ]


def test_message_text_joins_text_parts_only():
    assert Message("user", "plain").text() == "plain"
    assert Message("user", user_parts("look here", [IMG])).text() == "look here"
    assert Message("user", user_parts(None, [IMG])).text() == ""
    assert Message("assistant", None).text() == ""


import json  # noqa: E402

CALLS = [{"id": "c1", "type": "function", "function": {"name": "camera", "arguments": '{"question": "what"}'}}]


def test_pending_client_calls_lists_unanswered_calls_of_last_round():
    convo = Conversation(system_prompt="sys")
    convo.add_user("look")
    convo.add_tool_call("Let me look.", CALLS)
    assert convo.pending_client_calls() == ["c1"]
    convo.add_tool_result("c1", '{"image_attached": true}')
    assert convo.pending_client_calls() == []


def test_pending_client_calls_is_empty_after_a_plain_reply_and_on_fresh_conversations():
    convo = Conversation(system_prompt="sys")
    assert convo.pending_client_calls() == []
    convo.add_user("hi")
    convo.add_tool_call(None, CALLS)
    convo.add_tool_result("c1", "ok")
    convo.add_user(user_parts(None, [IMG]))
    convo.add_assistant("A mug.")
    assert convo.pending_client_calls() == []


def test_pending_client_calls_handles_partially_answered_rounds():
    calls = [
        {"id": "a", "type": "function", "function": {"name": "remember", "arguments": "{}"}},
        {"id": "b", "type": "function", "function": {"name": "camera", "arguments": "{}"}},
    ]
    convo = Conversation(system_prompt="sys")
    convo.add_user("hi")
    convo.add_tool_call(None, calls)
    convo.add_tool_result("a", "Remembered.")
    assert convo.pending_client_calls() == ["b"]


def test_seal_pending_appends_error_results_and_returns_ids():
    convo = Conversation(system_prompt="sys")
    convo.add_user("look")
    convo.add_tool_call(None, CALLS)
    assert convo.seal_pending("no result from client") == ["c1"]
    last = convo.history()[-1]
    assert last.role == "tool" and last.tool_call_id == "c1"
    assert json.loads(last.content) == {"error": "no result from client"}
    assert convo.pending_client_calls() == []
    assert convo.seal_pending("again") == []
