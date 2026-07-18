"""One realtime voice session: continuous audio in, Realtime-subset events out.

Threading model: feed_audio() is called by the transport and only enqueues; the
audio worker thread runs VAD/endpointing and partial transcription; each turn
(final STT → engine stream → chunked TTS) runs on its own thread so the audio
worker keeps listening — that is what makes barge-in possible. All output goes
through emit(dict), which must be thread-safe (server.py marshals to the WS).
"""
from __future__ import annotations

import queue
import threading

from richard.conversation import Conversation
from richard.errors import BrainUnreachable
from richard.realtime import events
from richard.realtime.chunker import ProgressiveChunker
from richard.realtime.vad import FRAME_BYTES

BRAIN_DOWN_LINE = "I can't reach my brain right now."


class RealtimeSession:
    def __init__(self, *, engine, transcriber, tts, detector, emit,
                 partial_every: int = 25, barge_in: str = "vad") -> None:
        self._engine = engine
        self._transcriber = transcriber
        self._tts = tts
        self._detector = detector
        self._emit = emit
        self._partial_every = partial_every
        self.barge_in = barge_in
        self.conversation = Conversation()
        self.state = "listening"
        self._frames_q: queue.Queue = queue.Queue()
        self._pending = bytearray()  # transport frames rebuffered to FRAME_BYTES
        self._closed = threading.Event()
        self._interrupt = threading.Event()
        self._turn_thread: threading.Thread | None = None
        self._frames_since_partial = 0
        self._input_item_id = ""
        self._audio_thread = threading.Thread(target=self._audio_worker, daemon=True)
        self._audio_thread.start()

    @property
    def samplerate(self) -> int:
        return getattr(self._tts, "samplerate", 24000)

    # -- transport-facing API (any thread) ----------------------------------

    def feed_audio(self, pcm: bytes) -> None:
        if not self._closed.is_set():
            self._frames_q.put(pcm)

    def create_text_item(self, text: str) -> None:
        text = (text or "").strip()
        if text:
            self._start_turn(lambda: text, announce_transcript=False)

    def cancel_response(self) -> None:
        self._interrupt.set()

    def update(self, patch: dict) -> dict:
        if patch.get("barge_in") in ("vad", "wake", "off"):
            self.barge_in = patch["barge_in"]
        return {"barge_in": self.barge_in}

    def close(self) -> None:
        self._closed.set()
        self._interrupt.set()
        self._frames_q.put(None)
        self._audio_thread.join(timeout=2.0)
        if self._turn_thread is not None:
            self._turn_thread.join(timeout=2.0)

    # -- audio worker ---------------------------------------------------------

    def _audio_worker(self) -> None:
        broken = False
        while True:
            data = self._frames_q.get()
            if data is None:
                return
            # With barge-in off (or wake, which is Phase 2), Richard is deaf
            # while a turn is running — otherwise his own speech loops back in.
            if self.barge_in != "vad" and self.state != "listening":
                self._pending.clear()
                continue
            if broken:
                continue  # pipeline already failed; keep draining so close() still works
            try:
                self._pending += data
                while len(self._pending) >= FRAME_BYTES:
                    frame = bytes(self._pending[:FRAME_BYTES])
                    del self._pending[:FRAME_BYTES]
                    self._handle_frame(frame)
            except Exception as exc:
                broken = True
                self._emit(events.error(f"audio pipeline failed: {exc}", code="internal_error"))

    def _handle_frame(self, frame: bytes) -> None:
        for event in self._detector.feed(frame):
            if event[0] == "speech_started":
                if self.state in ("thinking", "speaking"):
                    self._interrupt.set()  # vad barge-in
                self._input_item_id = events.new_id("item")
                self._frames_since_partial = 0
                self._emit(events.speech_started())
            elif event[0] == "utterance":
                self._emit(events.speech_stopped())
                pcm = event[1]
                self._start_turn(lambda: self._transcriber.final(pcm))
        if self._detector.in_speech:
            self._frames_since_partial += 1
            if self._frames_since_partial >= self._partial_every:
                self._frames_since_partial = 0
                try:
                    text = self._transcriber.partial(self._detector.collected())
                except Exception:
                    text = ""  # partials are best-effort; the final pass decides
                if text:
                    self._emit(events.transcription_delta(self._input_item_id, text))

    # -- turns ------------------------------------------------------------------

    def _start_turn(self, get_transcript, *, announce_transcript: bool = True) -> None:
        previous = self._turn_thread
        item_id = self._input_item_id

        def run() -> None:
            if previous is not None:
                previous.join(timeout=10.0)  # let an interrupted turn finish truncating
            self._interrupt.clear()  # anything set before this belonged to the previous turn
            self.state = "thinking"
            try:
                transcript = get_transcript()
            except Exception as exc:
                self._emit(events.error(f"transcription failed: {exc}", code="stt_error"))
                self.state = "listening"
                return
            if not transcript:
                self.state = "listening"
                return
            if announce_transcript:
                self._emit(events.transcription_completed(item_id, transcript))
            self._respond(transcript)

        self._turn_thread = threading.Thread(target=run, daemon=True)
        self._turn_thread.start()

    def _respond(self, user_text: str) -> None:
        self.conversation.add_user(user_text)
        response_id = events.new_id("resp")
        self._emit(events.response_created(response_id))
        chunker = ProgressiveChunker()
        full = ""
        status = "completed"
        try:
            for delta in self._engine.respond_streaming(self.conversation):
                if self._interrupt.is_set():
                    status = "cancelled"
                    break
                full += delta
                self._emit(events.text_delta(response_id, delta))
                if not all(self._speak(response_id, c) for c in chunker.feed(delta)):
                    status = "cancelled"
                    break
            if status == "completed":
                tail = chunker.flush()
                if tail and not self._speak(response_id, tail):
                    status = "cancelled"
        except BrainUnreachable:
            status = "failed"
            self._emit(events.error("brain unreachable", code="brain_unreachable"))
            self._speak(response_id, BRAIN_DOWN_LINE)
        except Exception as exc:
            status = "failed"
            self._emit(events.error(f"engine failed: {exc}", code="engine_error"))
        if full:
            self.conversation.add_assistant(full)
        if status == "cancelled":
            self._emit(events.item_truncated(response_id))
        self._emit(events.response_done(response_id, status))
        self.state = "listening"

    def _speak(self, response_id: str, text: str) -> bool:
        """Synth one chunk and emit it. False = interrupted (stop the response)."""
        if self._interrupt.is_set():
            return False
        self.state = "speaking"
        try:
            pcm = self._tts.synth(text)
        except Exception as exc:
            # One bad chunk must not kill the reply (SpeechPipeline philosophy).
            self._emit(events.error(f"tts failed on a chunk: {exc}", code="tts_error"))
            return True
        if self._interrupt.is_set():
            return False
        self._emit(events.audio_delta(response_id, pcm))
        return True
