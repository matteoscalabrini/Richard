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
