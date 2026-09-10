"""One realtime voice session: continuous audio in, Realtime-subset events out.

Threading model: feed_audio() is called by the transport and only enqueues; the
audio worker thread runs VAD/endpointing and partial transcription; one serial turn
worker runs final STT → engine stream → chunked TTS while the audio
worker keeps listening — that is what makes barge-in possible. All output goes
through emit(dict), which must be thread-safe (server.py marshals to the WS).
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque

from richard import vision
from richard.conversation import Conversation, Message
from richard.engine import ClientToolCall
from richard.errors import BrainRejectedInput, BrainUnreachable
from richard.perception.frames import validate_source_id
from richard.perception.image import data_url
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
    "Nobody addressed you; this is something you noticed. Consider it alongside the available "
    "conversation and memories. You may look more closely with an available tool, make an "
    "observation, ask a relevant question, or pick up a shared topic. Speaking is optional; "
    "avoid repeated greetings or questions. If speaking now would be unwelcome or you have "
    f"nothing worth saying, reply exactly {NOTHING_TO_SAY}."
)

log = logging.getLogger("richard.realtime")


class RealtimeSession:
    def __init__(self, *, engine, transcriber, tts, detector, emit,
                 partial_every: int = 25, barge_in: str = "vad",
                 registry=None, clock_hm=lambda: time.strftime("%H:%M"),
                 observation=None, source_change=None) -> None:
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
        self._observation = observation
        self._source_change = source_change
        self.source_id: str | None = None
        self.playback_ack = False
        self.visual_context = False
        self._context: list[str] = []
        self._context_lock = threading.Lock()
        self._attention_pending = False
        self._unsolicited = False  # the next turn was started by wake(), not by the user
        self._input_generation = 0
        # A client tool may split one unsolicited turn across several responses. Bind
        # its continuation to the input generation so fresh input always takes priority.
        self._unsolicited_continuation: int | None = None
        self._logical_turn_id: str | None = None
        self._continuation_open = False
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
        self._state_lock = threading.RLock()
        self._conversation_lock = threading.RLock()
        self._turn_thread: threading.Thread | None = None
        self._pending_turn = None
        self._queued_items: list[tuple[dict, bool]] = []
        self._active_token = None
        self._known_responses: set[str] = set()
        self._response_order: deque[str] = deque()
        self._playback_response_id: str | None = None
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
        if item["kind"] not in ("message", "function_call_output"):
            raise ValueError(f"unsupported item kind: {item['kind']}")
        with self._state_lock:
            content = item.get("content")
            has_text = item["kind"] == "message" and bool(
                Message(role="user", content=content).text().strip()
            )
            has_image = item["kind"] == "message" and bool(vision.image_urls(content))
            fresh = has_text or (has_image and not self._continuation_open)
            active = self._turn_thread is not None
            if active:
                if item["kind"] == "function_call_output":
                    pending = self.conversation.pending_client_calls()
                    queued_calls = {
                        queued["call_id"] for queued, _fresh in self._queued_items
                        if queued["kind"] == "function_call_output"
                    }
                    if item["call_id"] not in pending or item["call_id"] in queued_calls:
                        raise ValueError(
                            "function_call_output: unknown or already answered "
                            f"call_id {item['call_id']}"
                        )
                self._queued_items.append((item, fresh))
                if fresh:
                    self._reset_unsolicited()
                    self._interrupt.set()
                return
        with self._conversation_lock:
            self._apply_item(item, fresh=fresh)

    def _apply_item(self, item: dict, *, fresh: bool | None = None) -> None:
        if item["kind"] == "message":
            content = item["content"]
            if fresh is None:
                fresh = bool(Message(role="user", content=content).text().strip())
            if fresh:
                self._reset_unsolicited()
            self.conversation.add_user(content)
            urls = vision.image_urls(content)
            if urls:
                total = vision.count_images(self.conversation.history())
                for url in urls:
                    mime, nbytes = vision.check_image_data_url(url)
                    vision.log_image("client", mime, nbytes, total)
            return
        if item["call_id"] not in self.conversation.pending_client_calls():
            raise ValueError(f"function_call_output: unknown or already answered call_id {item['call_id']}")
        self.conversation.add_tool_result(item["call_id"], item["output"])

    def add_context(self, line: str) -> None:
        """Queue a `[perception]` line for the next turn (any thread)."""
        with self._context_lock:
            self._context.append(f"[perception] {self._clock_hm()} {line}")
            self._context = self._context[-8:]

    def _drain_context(self) -> str | None:
        with self._context_lock:
            lines, self._context = self._context, []
            self._attention_pending = False
        return "\n".join(lines) if lines else None

    def _has_context(self) -> bool:
        with self._context_lock:
            return bool(self._context)

    def _has_pending_attention(self) -> bool:
        with self._context_lock:
            return self._attention_pending and bool(self._context)

    def wake(self) -> bool:
        """Run an unsolicited turn on the queued context, if idle. False when nothing is
        queued or a response is active (the context then waits for the next turn)."""
        with self._context_lock:
            if self._context:
                self._attention_pending = True
        with self._state_lock:
            if not self._is_idle() or not self._has_context():
                log.info(
                    "perception wake skipped: state=%s queued=%s",
                    self.state, self._has_context(),
                )
                return False
            log.info("perception wake: unsolicited turn starting")
            self._reset_unsolicited()
            self._unsolicited = True
            self._start_turn(None)
        return True

    def _is_idle(self) -> bool:
        with self._state_lock:
            return (
                self.state == "listening"
                and self._turn_thread is None
                and not bool(getattr(self._detector, "in_speech", False))
                and self._playback_response_id is None
                and not self._closed.is_set()
            )

    def create_response(self) -> None:
        """Run a turn on the conversation as it stands (GA semantics)."""
        with self._state_lock:
            active = self._turn_thread is not None
            queued = bool(self._queued_items)
        if active and queued:
            self._start_turn(None)
            return
        if active or self.state != "listening":
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
        with self._state_lock:
            self._reset_unsolicited()
            self._interrupt.set()

    def _reset_unsolicited(self) -> None:
        with self._state_lock:
            self._input_generation += 1
            self._unsolicited = False
            self._unsolicited_continuation = None
            self._logical_turn_id = None
            self._continuation_open = False
            self._playback_response_id = None

    def update(self, patch: dict) -> dict:
        if patch.get("barge_in") in ("vad", "wake", "off"):
            self.barge_in = patch["barge_in"]
        if "source_id" in patch:
            source_id = validate_source_id(patch["source_id"])
            with self._state_lock:
                self.source_id = source_id
            if self._source_change is not None:
                self._source_change(source_id)
        if "tools" in patch:
            reserved = set(getattr(self._engine, "tool_names", lambda: [])())
            self.client_tools, self.dropped_tools = events.tools_to_schemas(patch["tools"], reserved)
        if "playback_ack" in patch:
            if not isinstance(patch["playback_ack"], bool):
                raise ValueError("session.update: 'playback_ack' must be a boolean")
            with self._state_lock:
                self.playback_ack = patch["playback_ack"]
                if not self.playback_ack:
                    self._playback_response_id = None
        if "visual_context" in patch:
            if not isinstance(patch["visual_context"], bool):
                raise ValueError("session.update: 'visual_context' must be a boolean")
            with self._state_lock:
                self.visual_context = patch["visual_context"]
        with self._state_lock:
            return {
                "barge_in": self.barge_in,
                "tools": [s["function"]["name"] for s in self.client_tools],
                "dropped_tools": list(self.dropped_tools),
                "source_id": self.source_id,
                "playback_ack": self.playback_ack,
                "visual_context": self.visual_context,
            }

    def playback_update(self, response_id: str, playing: bool) -> bool:
        with self._state_lock:
            if response_id not in self._known_responses:
                return False
            accepted = False
            if playing:
                accepted = self._playback_response_id == response_id
            elif self._playback_response_id == response_id:
                self._playback_response_id = None
                accepted = True
        if accepted and not playing:
            self.wake()
        return accepted

    def close(self) -> None:
        self._closed.set()
        if self._registry is not None:
            self._registry.remove(self)
        with self._state_lock:
            self._reset_unsolicited()
            self._interrupt.set()
            self._pending_turn = None
            self._playback_response_id = None
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
                with self._state_lock:
                    self._reset_unsolicited()
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
        """Queue the newest turn on one serial worker.

        Replacing a queued request never starts a second inference worker. The active
        request keeps its own cancellation Event, so it cannot clear or emit through
        the successor's ownership.
        """
        token = object()
        cancel = threading.Event()
        request = (token, cancel, get_transcript, announce_transcript, self._input_item_id)
        with self._state_lock:
            self._interrupt.set()
            self._active_token = token
            self._interrupt = cancel
            self._pending_turn = request
            self.state = "thinking"
            if self._turn_thread is not None:
                return
            self._turn_thread = threading.Thread(target=self._turn_loop, daemon=True)
            self._turn_thread.start()

    def _turn_loop(self) -> None:
        while not self._closed.is_set():
            with self._state_lock:
                request, self._pending_turn = self._pending_turn, None
                if request is None:
                    self.state = "listening"
                    retry_attention = (
                        self._has_pending_attention()
                        and not bool(getattr(self._detector, "in_speech", False))
                        and self._playback_response_id is None
                        and not self._closed.is_set()
                    )
                    if retry_attention:
                        self._reset_unsolicited()
                        self._unsolicited = True
                        token = object()
                        cancel = threading.Event()
                        self._active_token = token
                        self._interrupt = cancel
                        self._pending_turn = (
                            token, cancel, None, True, self._input_item_id,
                        )
                        self.state = "thinking"
                        continue
                    if self._turn_thread is threading.current_thread():
                        self._turn_thread = None
                    break
            token, cancel, get_transcript, announce_transcript, item_id = request
            self._run_turn(token, cancel, get_transcript, announce_transcript, item_id)

    def _run_turn(self, token, cancel, get_transcript, announce_transcript, item_id) -> None:
        queued = []
        with self._state_lock:
            if token is not self._active_token:
                return
            queued, self._queued_items = self._queued_items, []
            self.state = "thinking"
        with self._conversation_lock:
            for item, fresh in queued:
                self._apply_item(item, fresh=fresh)
            if get_transcript is None:
                self._respond(None, token, cancel)
                return
            try:
                transcript = get_transcript()
            except Exception as exc:
                if self._owns(token):
                    self._emit(events.error(f"transcription failed: {exc}", code="stt_error"))
                return
            if not transcript or not self._owns(token):
                return
            if announce_transcript and self._owns(token):
                self._emit(events.transcription_completed(item_id, transcript))
            self._respond(transcript, token, cancel)

    def _owns(self, token) -> bool:
        with self._state_lock:
            return token is self._active_token and not self._closed.is_set()

    def _emit_owned(self, token, event: dict, cancel: threading.Event | None = None) -> bool:
        with self._state_lock:
            if token is not self._active_token or self._closed.is_set():
                return False
            if cancel is not None and cancel.is_set():
                return False
            self._emit(event)
            return True

    def _remember_response(self, response_id: str) -> None:
        with self._state_lock:
            self._known_responses.add(response_id)
            self._response_order.append(response_id)
            while len(self._response_order) > 64:
                self._known_responses.discard(self._response_order.popleft())

    def _current_observation(self) -> tuple[str | None, list[dict] | None]:
        with self._state_lock:
            source_id = self.source_id
            enabled = self.visual_context
        shot = None
        if enabled and source_id is not None and self._observation is not None:
            try:
                shot = self._observation(source_id)
            except Exception as exc:
                log.warning("visual observation failed for source %s: %s", source_id, exc)
        if shot is None:
            return source_id, None
        jpeg, meta = shot
        age = max(0.0, float(meta.get("age_s", 0.0)))
        instruction = (
            f"Current visual observation from {source_id}; capture age {age:.2f} seconds. "
            "Use this visual evidence for the active conversation or action. "
            "Do not describe the scene unless the user requested a description."
        )
        return source_id, [
            {"type": "text", "text": instruction},
            {"type": "image_url", "image_url": {"url": data_url(jpeg)}},
        ]

    def _respond(self, user_text: str | None, token, cancel: threading.Event) -> None:
        observed_source, observation = self._current_observation()
        # A call the client never answered would leave a tool call without a result in
        # the served prefix; seal it so the model sees the failure instead of a broken prompt.
        with self._state_lock:
            if token is not self._active_token or self._closed.is_set():
                return
            self.conversation.seal_pending(NO_CLIENT_RESULT)
            input_generation = self._input_generation
            unsolicited = user_text is None and (
                self._unsolicited or self._unsolicited_continuation == input_generation
            )
            self._unsolicited = False
            self._unsolicited_continuation = None
            context = self._drain_context()
            if context:
                if unsolicited:
                    context += "\n" + UNSOLICITED_RULE
                self.conversation.add_user(context)
            if user_text is not None:
                self.conversation.add_user(user_text)
            if not self.visual_context or self.source_id != observed_source:
                observation = None
            self.conversation.set_observation(observation)
            if self._logical_turn_id is None:
                self._logical_turn_id = events.new_id("turn")
            turn_id = self._logical_turn_id
            response_id = events.new_id("resp")
            self._remember_response(response_id)
            self._emit(events.response_created(
                response_id, turn_id=turn_id, unsolicited=unsolicited,
            ))

        last_activity = None

        def activity(phase: str) -> None:
            nonlocal last_activity
            if phase == last_activity:
                return
            event = events.response_activity(
                response_id, turn_id, phase, unsolicited=unsolicited,
            )
            if self._emit_owned(token, event, cancel):
                last_activity = phase

        chunker = ProgressiveChunker()
        full = ""
        status = "completed"
        handed_to_client = False
        # Unsolicited turns hold their text back while it could still be the silence
        # sentinel; the first token that rules it out flushes everything held.
        holding = unsolicited
        held = ""
        try:
            for delta in self._engine.respond_streaming(
                self.conversation, client_tools=self.client_tools, observer=activity,
            ):
                if cancel.is_set() or not self._owns(token):
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
                    if self._owns(token):
                        self._continuation_open = True
                    tail = chunker.flush()
                    if tail and not self._speak(response_id, turn_id, unsolicited, tail, token, cancel):
                        status = "cancelled"
                        break
                    vision.log_client_call(delta.name, delta.arguments)
                    self._emit_owned(token, events.function_call_arguments_done(
                        response_id, delta.id, delta.name, delta.arguments), cancel)
                    continue
                full += delta
                self._emit_owned(token, events.text_delta(response_id, delta), cancel)
                if not all(
                    self._speak(response_id, turn_id, unsolicited, c, token, cancel)
                    for c in chunker.feed(delta)
                ):
                    status = "cancelled"
                    break
            if holding and held.strip():
                # The stream ended while held: the sentinel (stay silent) or a short reply
                # that merely looked like its start (speak it after all).
                if held.strip().rstrip(".").strip() != NOTHING_TO_SAY:
                    full += held
                    self._emit_owned(token, events.text_delta(response_id, held), cancel)
                    if not all(
                        self._speak(response_id, turn_id, unsolicited, c, token, cancel)
                        for c in chunker.feed(held)
                    ):
                        status = "cancelled"
                else:
                    log.info("unsolicited turn: the brain chose silence (%s)", NOTHING_TO_SAY)
            if unsolicited and full:
                log.info("unsolicited turn: spoke %d chars: %r", len(full), full[:120])
            if status == "completed" and not handed_to_client:
                tail = chunker.flush()
                if tail and not self._speak(response_id, turn_id, unsolicited, tail, token, cancel):
                    status = "cancelled"
        except BrainRejectedInput as exc:
            status = "failed"
            activity("error")
            self._emit_owned(token, events.error(str(exc), code="brain_rejected_input"))
            self._speak(response_id, turn_id, unsolicited, IMAGE_REJECTED_LINE, token, cancel)
        except BrainUnreachable:
            status = "failed"
            activity("error")
            self._emit_owned(token, events.error("brain unreachable", code="brain_unreachable"))
            self._speak(response_id, turn_id, unsolicited, BRAIN_DOWN_LINE, token, cancel)
        except Exception as exc:
            status = "failed"
            activity("error")
            self._emit_owned(token, events.error(f"engine failed: {exc}", code="engine_error"))
            # Audible failure: silence here reads as a crash to the user.
            self._speak(response_id, turn_id, unsolicited, TURN_FAILED_LINE, token, cancel)
        # A turn handed to the client already stored its text inside the tool-call
        # message; storing it again would put a plain reply after the call.
        owned = self._owns(token)
        if cancel.is_set() or not owned:
            status = "cancelled"
        if full and not handed_to_client and status == "completed":
            with self._state_lock:
                if token is self._active_token and not cancel.is_set():
                    self.conversation.add_assistant(full)
        if status == "cancelled":
            self._emit(events.item_truncated(response_id))
        if (unsolicited and handed_to_client and status == "completed"
                and not cancel.is_set() and owned):
            self._unsolicited_continuation = input_generation
        if handed_to_client and status == "completed" and owned:
            pass  # keep the logical turn id for the client's continuation
        elif owned:
            self._logical_turn_id = None
            self._continuation_open = False
        # Listening again BEFORE response.done goes out: the Reachy app posts the tool
        # output and response.create the moment it sees response.done, and must not be
        # told the response is still active.
        if owned:
            self.state = "listening"
        self._emit(events.response_done(response_id, status))

    def _speak(self, response_id: str, turn_id: str, unsolicited: bool, text: str,
               token, cancel: threading.Event) -> bool:
        """Synth one chunk and emit it. False = interrupted (stop the response)."""
        if cancel.is_set() or not self._owns(token):
            return False
        with self._state_lock:
            if token is not self._active_token or cancel.is_set() or self._closed.is_set():
                return False
            self.state = "speaking"
        try:
            pcm = self._tts.synth(text)
        except Exception as exc:
            # One bad chunk must not kill the reply (SpeechPipeline philosophy).
            self._emit_owned(token, events.error(f"tts failed on a chunk: {exc}", code="tts_error"))
            return True
        with self._state_lock:
            if token is not self._active_token or cancel.is_set() or self._closed.is_set():
                return False
            self._emit(events.response_activity(
                response_id, turn_id, "answer", unsolicited=unsolicited,
            ))
            if self.playback_ack:
                self._playback_response_id = response_id
            self._emit(events.audio_delta(response_id, pcm))
        return True
