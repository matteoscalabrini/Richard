from __future__ import annotations

from typing import Callable

from richard.conversation import Conversation
from richard.errors import BrainUnreachable

EXIT_COMMANDS = {"exit", "quit", ":q"}


def run_repl(
    engine,
    conversation: Conversation,
    read: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
) -> None:
    write("Richard is listening. Type 'exit' to quit.")
    while True:
        try:
            user_input = read("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            write("")
            return
        if not user_input:
            continue
        if user_input.lower() in EXIT_COMMANDS:
            return
        conversation.add_user(user_input)
        try:
            reply = engine.respond(conversation)
        except BrainUnreachable:
            write("richard> I can't reach my brain right now.")
            continue
        conversation.add_assistant(reply)
        write(f"richard> {reply}")
