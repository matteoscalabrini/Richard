from __future__ import annotations

import asyncio
import queue
import time

from richard.satellite.protocol import Hello, SessionStart, decode, encode


class WsRelayConnection:
    """Adapts one websocket to the synchronous RelayConnection seam.

    `ws` must provide sync `send_text(str)` and `send_bytes(bytes)` (the real serve()
    passes a loop-marshaling adapter; tests pass a simple fake). The async reader calls
    `dispatch(text)` for control frames and `push_audio(bytes)` for binary frames; when the
    turn ends or the socket closes it calls `end_audio()` to release any blocked frame iterator.
    The manager runs on a worker thread and uses the blocking methods below.
    """

    # Downlink TTS pacing: small embedded satellites exhaust their receive
    # buffers whenever audio arrives much faster than the firmware drains it
    # through the speaker (16 kHz mono int16 = 32 KB/s). Chunk size alone
    # doesn't help; the send RATE must track playback. So: burst a small
    # prefill for jitter headroom, then pace each chunk at just under its own
    # playback duration — in-flight backlog stays bounded at roughly the
    # prefill regardless of reply length. 4000 bytes = 2000 samples = 125 ms
    # of audio (even → sample-aligned); 0.12 s pace ≈ 4% send-ahead.
    AUDIO_CHUNK_BYTES = 4000
    AUDIO_CHUNK_PACE_S = 0.12
    AUDIO_PREFILL_CHUNKS = 4

    def __init__(self, ws) -> None:
        self._ws = ws
        self._audio: queue.Queue = queue.Queue()

    # outbound (manager worker thread → hub)
    def send(self, msg) -> None:
        self._ws.send_text(encode(msg))

    def send_audio(self, frame: bytes) -> None:
        for i, off in enumerate(range(0, len(frame), self.AUDIO_CHUNK_BYTES)):
            self._ws.send_bytes(frame[off:off + self.AUDIO_CHUNK_BYTES])
            past_prefill = i >= self.AUDIO_PREFILL_CHUNKS - 1
            if past_prefill and off + self.AUDIO_CHUNK_BYTES < len(frame):
                time.sleep(self.AUDIO_CHUNK_PACE_S)

    # inbound, fed by the async reader
    def dispatch(self, text: str):
        return decode(text)

    def push_audio(self, frame: bytes) -> None:
        self._audio.put(frame)

    def end_audio(self) -> None:
        self._audio.put(None)

    def begin_utterance(self) -> None:
        """Drop any audio buffered since the previous utterance (start a fresh capture)."""
        while True:
            try:
                self._audio.get_nowait()
            except queue.Empty:
                break

    # RelayConnection API (called from the manager worker thread)
    def audio_frames(self):
        while True:
            frame = self._audio.get()
            if frame is None:
                return
            yield frame


class _LoopBoundWs:
    """Wraps a real async websockets connection so WsRelayConnection's sync send_text/
    send_bytes work from a worker thread: each send is marshalled onto the event loop."""

    def __init__(self, ws, loop: asyncio.AbstractEventLoop) -> None:
        self._ws = ws
        self._loop = loop

    def send_text(self, data: str) -> None:
        asyncio.run_coroutine_threadsafe(self._ws.send(data), self._loop).result()

    def send_bytes(self, data: bytes) -> None:
        asyncio.run_coroutine_threadsafe(self._ws.send(data), self._loop).result()


async def handle_connection(manager, ws) -> None:
    """Drive one relay connection. The reader loop keeps pumping inbound audio + control
    frames while a turn runs CONCURRENTLY on a worker thread — never awaited inline, which
    would starve the very audio/results the turn is blocked waiting for."""
    loop = asyncio.get_running_loop()
    conn = WsRelayConnection(_LoopBoundWs(ws, loop))
    hello: Hello | None = None
    turn = None
    try:
        async for raw in ws:
            if isinstance(raw, (bytes, bytearray)):
                conn.push_audio(bytes(raw))
                continue
            msg = conn.dispatch(raw)
            if isinstance(msg, Hello):
                hello = msg
                manager.register_relay(hello.relay_id, conn)
            elif isinstance(msg, SessionStart) and hello and (turn is None or turn.done()):
                conn.begin_utterance()
                room_name = hello.room_name
                turn = loop.run_in_executor(
                    None, lambda rn=room_name: manager.run_turn(conn, room_name=rn))
    finally:
        conn.end_audio()  # release a turn blocked in audio_frames() if the socket dropped
        if turn is not None:
            try:
                await turn
            except Exception:
                pass  # turn failed during teardown (e.g. a send on a closed socket) — nothing to do
        if hello:
            manager.unregister_relay(hello.relay_id)


async def serve(manager, host: str, port: int, web_app=None, web_host: str | None = None,
                web_port: int | None = None, web_ssl=None):
    """Run the WebSocket relay server. One handle_connection() task per connection.

    If ``web_app`` is given, a config web UI is served alongside the relay on
    ``web_host``:``web_port`` (defaulting to the relay host/port+1), over HTTPS when
    ``web_ssl`` is provided. Both servers run concurrently for the lifetime of the process.
    """
    import websockets

    async def handler(ws):
        await handle_connection(manager, ws)

    async with websockets.serve(handler, host, port):
        if web_app is not None:
            from richard.web import serve_web

            wh = web_host if web_host is not None else host
            wp = web_port if web_port is not None else port + 1
            asyncio.ensure_future(serve_web(web_app, wh, wp, ssl_context=web_ssl))
        await asyncio.Future()  # run forever
