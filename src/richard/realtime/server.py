"""WebSocket transport for realtime sessions: /v1/realtime (OpenAI Realtime subset).

One RealtimeSession per connection. Inbound: JSON client events (audio arrives
base64-encoded inside input_audio_buffer.append — no binary frames, matching the
Realtime API). Outbound: the session emits dicts from worker threads; emit()
marshals them onto the event loop where a single sender task owns ws.send.
"""
from __future__ import annotations

import asyncio
import json
import secrets
from urllib.parse import parse_qs, urlparse

from richard.realtime import events


def _authorized(ws, token: str) -> bool:
    request = getattr(ws, "request", None)
    path = getattr(request, "path", "") or ""
    query_token = parse_qs(urlparse(path).query).get("token", [""])[0]
    headers = getattr(request, "headers", None) or {}
    bearer = headers.get("Authorization", "")
    return secrets.compare_digest(query_token, token) or secrets.compare_digest(bearer, f"Bearer {token}")


def _text_content(event: dict) -> str:
    """Extract the text of a conversation.item.create (subset: input_text parts only)."""
    item = event.get("item")
    if not isinstance(item, dict):
        return ""
    parts = item.get("content")
    if not isinstance(parts, list):
        return ""
    return " ".join(
        p["text"] for p in parts
        if isinstance(p, dict) and p.get("type") == "input_text" and isinstance(p.get("text"), str)
    ).strip()


async def handle_realtime(ws, session_factory, *, token: str = "") -> None:
    if token and not _authorized(ws, token):
        await ws.close(code=4001, reason="unauthorized")
        return

    loop = asyncio.get_running_loop()
    out_q: asyncio.Queue = asyncio.Queue()

    def emit(event: dict) -> None:
        # Called from session worker threads — hop onto the loop.
        loop.call_soon_threadsafe(out_q.put_nowait, event)

    session = session_factory(emit)
    emit(events.session_created(events.new_id("sess"),
                                output_samplerate=getattr(session, "samplerate", 24000)))

    async def sender() -> None:
        while True:
            event = await out_q.get()
            if event is None:
                return
            try:
                await ws.send(json.dumps(event))
            except Exception:
                return  # peer gone; the receive loop will wind down too

    send_task = asyncio.create_task(sender())
    try:
        async for raw in ws:
            try:
                event = events.parse_client_event(raw)
            except ValueError as exc:
                emit(events.error(str(exc)))
                continue
            etype = event["type"]
            try:
                if etype == "input_audio_buffer.append":
                    session.feed_audio(event["audio"])
                elif etype == "response.cancel":
                    session.cancel_response()
                elif etype == "session.update":
                    patch = event.get("session")
                    if not isinstance(patch, dict):
                        emit(events.error("session.update: 'session' must be an object"))
                        continue
                    emit(events.session_updated(session.update(patch)))
                elif etype == "conversation.item.create":
                    if not isinstance(event.get("item"), dict):
                        emit(events.error("conversation.item.create: 'item' must be an object"))
                        continue
                    text = _text_content(event)
                    if text:
                        session.create_text_item(text)
            except Exception:
                # A malformed-but-parseable event must never kill the connection.
                emit(events.error(f"internal error handling {etype}", code="internal_error"))
    finally:
        try:
            await asyncio.to_thread(session.close)
        finally:
            out_q.put_nowait(None)
        try:
            await asyncio.wait_for(send_task, timeout=5.0)
        except asyncio.TimeoutError:
            send_task.cancel()


async def serve_realtime(session_factory, host: str, port: int, *,
                         token: str = "", ssl_context=None) -> None:
    import websockets

    async def handler(ws):
        path = getattr(getattr(ws, "request", None), "path", "") or ""
        if urlparse(path).path != "/v1/realtime":
            await ws.close(code=4004, reason="unknown path")
            return
        await handle_realtime(ws, session_factory, token=token)

    async with websockets.serve(handler, host, port, ssl=ssl_context):
        await asyncio.Future()  # run forever
