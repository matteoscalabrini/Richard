from __future__ import annotations

from typing import Callable

from richard.conversation import Conversation
from richard.errors import BrainUnreachable
from richard.voice.segmenter import SentenceSegmenter

EXIT_COMMANDS = {"exit", "quit", ":q"}


def run_voice_loop(
    engine,
    conversation: Conversation,
    stt,
    speech,
    record_utterance: Callable[[], bytes],
    *,
    samplerate: int = 16000,
    streaming: bool = True,
    read: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
) -> None:
    write("Richard is listening. Press Enter to talk, type 'exit' to quit.")
    while True:
        try:
            command = read("[Enter to talk] ").strip()
        except (EOFError, KeyboardInterrupt):
            write("")
            speech.stop()
            return
        if command.lower() in EXIT_COMMANDS:
            speech.stop()
            return
        pcm = record_utterance()
        try:
            transcript = stt.transcribe(pcm, samplerate)
        except Exception:
            write("(couldn't transcribe — say again)")
            continue
        if not transcript:
            write("(heard nothing — say again)")
            continue
        write(f"you> {transcript}")
        conversation.add_user(transcript)
        segmenter = SentenceSegmenter()
        full = ""
        try:
            for delta in engine.respond_streaming(conversation):
                full += delta
                if streaming:
                    for sentence in segmenter.feed(delta):
                        speech.say(sentence)
            if streaming:
                tail = segmenter.flush()
                if tail:
                    speech.say(tail)
            elif full.strip():
                # Whole-utterance synthesis: best prosody / natural pauses (remote GPU TTS).
                speech.say(full.strip())
        except BrainUnreachable:
            # Abandon the half-spoken reply but keep the pipeline alive for the
            # next turn — stop() would latch Richard mute for the whole session.
            speech.clear()
            write("richard> I can't reach my brain right now.")
            continue
        conversation.add_assistant(full)
        write(f"richard> {full}")
        speech.drain()
