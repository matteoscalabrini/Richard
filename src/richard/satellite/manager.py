from __future__ import annotations

import time

from richard.conversation import Conversation
from richard.engine import Engine
from richard.errors import BrainUnreachable
from richard.satellite.audio_resample import resample_pcm16
from richard.satellite.connection import RelayConnection
from richard.satellite.protocol import State
from richard.satellite.relays import RelayRegistry
from richard.satellite.room import RoomContextProvider
from richard.voice.segmenter import SentenceSegmenter
from richard.voice.vad import record_utterance


class SatelliteManager:
    def __init__(self, *, brain, memory_provider, personality, stt, synthesizer,
                 relays: RelayRegistry, vad_factory=None, samplerate: int = 16000,
                 clock=time.monotonic, carry_window_s: float = 180.0,
                 max_carry_turns: int = 6, extra_providers=()) -> None:
        self._brain = brain
        self._memory_provider = memory_provider
        self._personality = personality
        self._stt = stt
        self._synth = synthesizer
        self._relays = relays
        self._vad_factory = vad_factory
        self._samplerate = samplerate
        self._clock = clock
        self._carry_window_s = carry_window_s
        self._max_carry_turns = max_carry_turns
        self._extra_providers = list(extra_providers)
        self._carry: list[tuple[float, str, str, str]] = []  # (t, room, user, assistant)

    def _engine(self, room_name: str | None) -> Engine:
        providers = [
            RoomContextProvider(room_name),
            self._memory_provider,
            *self._extra_providers,
        ]
        return Engine(self._brain, providers, self._personality)

    def _conversation(self, room_name: str | None, transcript: str) -> Conversation:
        convo = Conversation()
        now = self._clock()
        for t, _room, user, assistant in self._carry:
            if now - t <= self._carry_window_s:
                convo.add_user(user)
                convo.add_assistant(assistant)
        convo.add_user(transcript)
        return convo

    def _remember_turn(self, room_name: str | None, user: str, assistant: str) -> None:
        self._carry.append((self._clock(), room_name or "", user, assistant))
        if len(self._carry) > self._max_carry_turns:
            self._carry = self._carry[-self._max_carry_turns:]

    def register_relay(self, relay_id, conn):
        self._relays.register(relay_id, conn)

    def unregister_relay(self, relay_id):
        self._relays.unregister(relay_id)

    def run_turn(self, conn: RelayConnection, *, room_name: str | None) -> None:
        conn.send(State(state="listening"))
        vad = self._vad_factory() if self._vad_factory is not None else None
        pcm = record_utterance(conn.audio_frames(), self._samplerate, vad=vad)
        conn.send(State(state="thinking"))
        transcript = self._stt.transcribe(pcm, self._samplerate)
        if not transcript:
            conn.send(State(state="idle"))
            return
        engine = self._engine(room_name)
        convo = self._conversation(room_name, transcript)
        segmenter = SentenceSegmenter()
        reply = ""
        speaking = False

        def speak(text: str) -> None:
            nonlocal speaking
            if not speaking:
                conn.send(State(state="speaking"))
                speaking = True
            audio = self._synth.synth(text)
            src_rate = getattr(self._synth, "samplerate", self._samplerate)
            conn.send_audio(resample_pcm16(audio, src_rate, self._samplerate))

        try:
            for delta in engine.respond_streaming(convo):
                reply += delta
                for sentence in segmenter.feed(delta):
                    speak(sentence)
            tail = segmenter.flush()
            if tail:
                speak(tail)
            self._remember_turn(room_name, transcript, reply)
        except BrainUnreachable:
            speak("I can't reach my brain right now.")
        finally:
            conn.send(State(state="idle"))
