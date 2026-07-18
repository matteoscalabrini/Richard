"""Realtime voice core: continuous audio in, OpenAI-Realtime-subset events out.

The public contract is documented in docs/realtime-api.md. Transport lives in
server.py; per-connection pipeline logic in session.py; the stages (VAD, STT,
chunking) are plain injectable classes so everything unit-tests without models.
"""
