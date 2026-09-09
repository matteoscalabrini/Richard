"""One realtime voice session: continuous audio in, Realtime-subset events out.

Threading model: feed_audio() is called by the transport and only enqueues; the
audio worker thread runs VAD/endpointing and partial transcription; each turn
(final STT → engine stream → chunked TTS) runs on its own thread so the audio
worker keeps listening — that is what makes barge-in possible. All output goes
through emit(dict), which must be thread-safe (server.py marshals to the WS).
"""
from __future__ import annotations

import logging
import queue
import threading
import time

from richard import vision
from richard.conversation import Conversation
from richard.engine import ClientToolCall
from richard.errors import BrainRejectedInput, BrainUnreachable
from richard.realtime import events
from richard.realtime.chunker import ProgressiveChunker
from richard.realtime.vad import FRAME_BYTES

BRAIN_DOWN_LINE = "I can't reach my brain right now."
TURN_FAILED_LINE = "Something went wrong on my end."
IMAGE_REJECTED_LINE = "I couldn't take that picture in."
NO_CLIENT_RESULT = "no result from client"
# An unsolicited turn (a perception wake-up) may decide to stay silent. The sentinel is
# held back from the chunker and never spoken or stored (same idiom as NOTHING_TO_RUN).
NOTHING_TO_SAY = "NOTHING_TO_SAY"
UNSOLICITED_RULE = (
    "Nobody addressed you; this is something you noticed. A brief greeting or a remark "
    "about what you noticed is welcome. If speaking now would be unwelcome or pointless, "
    f"reply exactly {NOTHING_TO_SAY}."
)

log = logging.getLogger("richard.realtime")


class RealtimeSession:
    def __init__(self, *, engine, transcriber, tts, detector, emit,
                 partial_every: int = 25, barge_in: str = "vad",
                 registry=None, clock_hm=lambda: time.strftime("%H:%M")) -> None:
        self._engine = engine
        self._transcriber = transcriber
        self._tts = tts
        self._detector = detector
        self._emit = emit
        self._partial_every = partial_every
        self.barge_in = barge_in
        # Context lines (perception) queued for the next turn, served as one user-role
        # item before the user's own text. The registry lets the rest of serve find us.
        self._registry = registry
        self._clock_hm = clock_hm
        self._context: list[str] = []
        self._context_lock = threading.Lock()
        self._unsolicited = False  # the next turn was started by wake(), not by the user
        # Tools the connected client owns (the Reachy app's camera, the browser's
        # webcam), as chat-completions schemas. The engine defers their calls to us.
        self.client_tools: list[dict] = []
        self.dropped_tools: list[str] = []
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
        if registry is not None:
            registry.add(self)

    @property
    def samplerate(self) -> int:
        return getattr(self._tts, "samplerate", 24000)

    # -- transport-facing API (any thread) ----------------------------------

    def feed_audio(self, pcm: bytes) -> None:
        if not self._closed.is_set():
            self._frames_q.put(pcm)

    def create_item(self, item: dict) -> None:
        """Append a parsed conversation item (see events.parse_item). Append only: no turn
        starts until response.create. Raises ValueError when the item cannot be appended."""
        if item["kind"] == "message":
            content = item["content"]
            self.conversation.add_user(content)
            urls = vision.image_urls(content)
            if urls:
                total = vision.count_images(self.conversation.history())
                for url in urls:
                    mime, nbytes = vision.check_image_data_url(url)
                    vision.log_image("client", mime, nbytes, total)
            return
        if item["kind"] == "function_call_output":
            if item["call_id"] not in self.conversation.pending_client_calls():
                raise ValueError(f"function_call_output: unknown or already answered call_id {item['call_id']}")
            self.conversation.add_tool_result(item["call_id"], item["output"])
            return
        raise ValueError(f"unsupported item kind: {item['kind']}")

    def add_context(self, line: str) -> None:
        """Queue a `[perception]` line for the next turn (any thread)."""
        with self._context_lock:
            self._context.append(f"[perception] {self._clock_hm()} {line}")

    def _drain_context(self) -> str | None:
        with self._context_lock:
            lines, self._context = self._context, []
        return "\n".join(lines) if lines else None

    def _has_context(self) -> bool:
        with self._context_lock:
            return bool(self._context)

    def wake(self) -> bool:
        """Run an unsolicited turn on the queued context, if idle. False when nothing is
        queued or a response is active (the context then waits for the next turn)."""
        if self.state != "listening" or not self._has_context():
            log.info("perception wake skipped: state=%s queued=%s", self.state, self._has_context())
            return False
        log.info("perception wake: unsolicited turn starting")
        self._unsolicited = True
        self._start_turn(None)
        return True

    def create_response(self) -> None:
        """Run a turn on the conversation as it stands (GA semantics)."""
        if self.state != "listening":
            self._emit(events.error("conversation already has an active response",
                                    code=events.ACTIVE_RESPONSE_CODE))
            return
        history = self.conversation.history()
        nothing_new = not self._has_context() and (not history or (
            history[-1].role == "assistant" and not self.conversation.pending_client_calls()
        ))
        if nothing_new:
            response_id = events.new_id("resp")
            self._emit(events.response_created(response_id))
            self._emit(events.response_done(response_id))
            return
        self._start_turn(None)

    def cancel_response(self) -> None:
        self._interrupt.set()

    def update(self, patch: dict) -> dict:
        if patch.get("barge_in") in ("vad", "wake", "off"):
            self.barge_in = patch["barge_in"]
        if "tools" in patch:
            reserved = set(getattr(self._engine, "tool_names", lambda: [])())
            self.client_tools, self.dropped_tools = events.tools_to_schemas(patch["tools"], reserved)
        return {
            "barge_in": self.barge_in,
            "tools": [s["function"]["name"] for s in self.client_tools],
            "dropped_tools": list(self.dropped_tools),
        }

    def close(self) -> None:
        if self._registry is not None:
            self._registry.remove(self)
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
        """Run a turn on its own thread. `get_transcript` yields the new user text, or is
        None for a response.create on the conversation as it stands."""
        previous = self._turn_thread
        item_id = self._input_item_id
        self.state = "thinking"  # claimed now, so a second response.create sees it active

        def run() -> None:
            if previous is not None:
                previous.join(timeout=10.0)  # let an interrupted turn finish truncating
            self._interrupt.clear()  # anything set before this belonged to the previous turn
            self.state = "thinking"
            if get_transcript is None:
                self._respond(None)
                return
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

    def _respond(self, user_text: str | None) -> None:
        # A call the client never answered would leave a tool call without a result in
        # the served prefix; seal it so the model sees the failure instead of a broken prompt.
        self.conversation.seal_pending(NO_CLIENT_RESULT)
        unsolicited, self._unsolicited = self._unsolicited, False
        context = self._drain_context()
        if context:
            if unsolicited:
                context += "\n" + UNSOLICITED_RULE
            self.conversation.add_user(context)
        if user_text is not None:
            self.conversation.add_user(user_text)
        response_id = events.new_id("resp")
        self._emit(events.response_created(response_id))
        chunker = ProgressiveChunker()
        full = ""
        status = "completed"
        handed_to_client = False
        # Unsolicited turns hold their text back while it could still be the silence
        # sentinel; the first token that rules it out flushes everything held.
        holding = unsolicited
        held = ""
        try:
            for delta in self._engine.respond_streaming(self.conversation, client_tools=self.client_tools):
                if self._interrupt.is_set():
                    status = "cancelled"
                    break
                if holding and isinstance(delta, str):
                    held += delta
                    if NOTHING_TO_SAY.startswith(held.strip().rstrip(".").strip()):
                        continue
                    holding = False
                    delta, held = held, ""
                if isinstance(delta, ClientToolCall):
                    # The engine recorded the call and ends the turn; the client runs the
                    # tool and asks for a new response. Speak what was said before it.
                    handed_to_client = True
                    tail = chunker.flush()
                    if tail and not self._speak(response_id, tail):
                        status = "cancelled"
                        break
                    vision.log_client_call(delta.name, delta.arguments)
                    self._emit(events.function_call_arguments_done(
                        response_id, delta.id, delta.name, delta.arguments))
                    continue
                full += delta
                self._emit(events.text_delta(response_id, delta))
                if not all(self._speak(response_id, c) for c in chunker.feed(delta)):
                    status = "cancelled"
                    break
            if holding and held.strip():
                # The stream ended while held: the sentinel (stay silent) or a short reply
                # that merely looked like its start (speak it after all).
                if held.strip().rstrip(".").strip() != NOTHING_TO_SAY:
                    full += held
                    self._emit(events.text_delta(response_id, held))
                    if not all(self._speak(response_id, c) for c in chunker.feed(held)):
                        status = "cancelled"
                else:
                    log.info("unsolicited turn: the brain chose silence (%s)", NOTHING_TO_SAY)
            if unsolicited and full:
                log.info("unsolicited turn: spoke %d chars: %r", len(full), full[:120])
            if status == "completed" and not handed_to_client:
                tail = chunker.flush()
                if tail and not self._speak(response_id, tail):
                    status = "cancelled"
        except BrainRejectedInput as exc:
            status = "failed"
            self._emit(events.error(str(exc), code="brain_rejected_input"))
            self._speak(response_id, IMAGE_REJECTED_LINE)
        except BrainUnreachable:
            status = "failed"
            self._emit(events.error("brain unreachable", code="brain_unreachable"))
            self._speak(response_id, BRAIN_DOWN_LINE)
        except Exception as exc:
            status = "failed"
            self._emit(events.error(f"engine failed: {exc}", code="engine_error"))
            # Audible failure: silence here reads as a crash to the user.
            self._speak(response_id, TURN_FAILED_LINE)
        # A turn handed to the client already stored its text inside the tool-call
        # message; storing it again would put a plain reply after the call.
        if full and not handed_to_client:
            self.conversation.add_assistant(full)
        if status == "cancelled":
            self._emit(events.item_truncated(response_id))
        # Listening again BEFORE response.done goes out: the Reachy app posts the tool
        # output and response.create the moment it sees response.done, and must not be
        # told the response is still active.
        self.state = "listening"
        self._emit(events.response_done(response_id, status))

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
