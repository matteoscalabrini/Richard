# Vision

Richard sees through pictures. Three paths, one core.

- **Typed chat and push-to-talk (web UI).** The ⊡ button attaches a picture (a phone opens
  its camera). The browser resizes it to an 800 px long edge (about 474 prompt tokens on the
  production brain) and sends it as an OpenAI content part on `/api/chat` or `/api/voice`.
  The page keeps the picture in its history, so later turns re-send it.
- **Voice mode (web UI).** The page offers a `camera` tool over `/v1/realtime`. When the brain
  decides to look it calls the tool; the page takes a webcam frame (800 px, or 1600 px when
  the brain asks for `detail: "high"`) and posts it exactly as the Reachy app does. Say
  "what am I holding?" and watch the entity switch to "looking…".
- **Robot.** The stock Reachy Mini Conversation App's `camera` tool follows the same sequence;
  see `docs/realtime-api.md`. The app sends its native frame (720p class, about 1200 tokens).

What Richard is told: he sees only through pictures, says so when he has none, and treats a
picture as one moment from one viewpoint (`richard/persona.py`, `PERCEPTION_RULES`).

Operations: images stay in the conversation for the session (append-only prompts keep the
prefix cache warm); INFO lines on the `richard.vision` logger record each image, each request
with images in history, and each client tool call. A brain without vision answers 4xx:
Richard says "I couldn't take that picture in." and does not retry.

Next: the ambient layer (`docs/superpowers/specs/2026-09-09-ambient-perception-design.md`).
