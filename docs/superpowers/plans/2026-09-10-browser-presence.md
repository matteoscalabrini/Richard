# Browser Presence Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development to implement and review each task. Execute inline in the current session without further approval checkpoints.

**Goal:** Make browser conversations feel continuous through cached waiting speech, playback-aware turns, and fresh visual context.

**Architecture:** Prepare short speech clips outside conversation turns and serve a matching cache over HTTP. The realtime server reports factual processing phases and logical turn identity; the browser owns audible scheduling and cancellation. Bind the browser frame source to its realtime session, supplying a bounded current observation and reconsidering relevant scene changes when speech ends.

**Tech Stack:** Existing Python, pytest, browser Web Audio, JavaScript tested with Node's built-in test runner. No new production dependencies.

**Spec:** `../specs/2026-09-10-richard-continuous-presence-proposal.md`, section “Next browser milestone”, approved by Matteo on 2026-09-10.

## Global Constraints

- Work in the existing Richard `reachy-presence` worktree, starting at `0577aff`. Keep project text in English.
- Keep the stock Reachy app and SDK unmodified. Current acceptance is the browser; physical Reachy has not arrived.
- No vector database, embeddings, or unbounded frame/history accumulation.
- Preserve ACTION_RULES, verification result prefixes, custom persona overrides, and unsolicited silence.
- No production LLM prompts. Use fake external model/TTS boundaries in tests; real TTS preparation and listening checks are separate from model inference.
- Speech clips: approximately 1.2 seconds of audible silence, one per interval, at most two per logical user turn, at least eight seconds apart, no immediate repeated variant. Answer and user speech take priority. No clips on unsolicited turns.
- Missing/invalid/wrong-voice clips mean silence. Never synthesize them on an active conversational request.
- Tool-specific phase information must come from execution/results, not a timer or guessing from user words. A visual waiting clip requires an available image.
- Use meaningful behavioral tests with an observed red phase. Preserve unrelated working changes. Workers never spawn subagents; reviewers are assigned centrally.

### Task 1: Prepared voice-cue cache and read-only serving

**Files:** Create `src/richard/realtime/cues.py`, `tests/test_realtime_cues.py`; modify `src/richard/web/app.py` and its relevant tests; update `docs/realtime-api.md` for the preparation command and read endpoint.

**Interfaces:**

```python
def cue_fingerprint(config, language: str) -> str: ...
def read_cues(config, directory=None) -> dict: ...
def prepare_cues(config, directory=None, *, synth=None, force=False) -> dict: ...
```

`python -m richard.realtime.cues prepare [--config PATH] [--force]` prepares the configured voice with existing `_build_tts`. Default cache is below the config/data home, never inside the repository. Select Italian for Italian/it configuration, English for English/en or auto/empty; unsupported explicit languages return an empty bank rather than speaking another language. The bank contains three short thinking variants and three visual variants in that language. Use the configured voice/effect; include every TTS-affecting setting, the selected language, and catalog revision/content in its fingerprint. Do not persist credentials or endpoints in manifest metadata. No startup/HTTP synthesis or hidden background GPU job.

HTTP `GET /api/realtime/cues` loads current config and returns:

```json
{"fingerprint":"opaque hash","language":"en","clips":[{"id":"thinking-0","phase":"thinking","text":"Mm, let me think.","audio":"PCM16 base64","sample_rate":24000}]}
```

An absent/corrupt/nonmatching bank returns the same envelope with `clips: []`. Writes are atomic; a failed preparation cannot expose a partial bank. Idempotent preparation reuses a valid bank. `--force` supports replacing a voice sample under the same voice identifier. Validate nonempty PCM16 and sensible positive sample rate/length when reading.

- [x] Write tests before implementation for cache reuse without synthesis, voice/effect/language invalidation, corruption and partial failure, configured language selection, and HTTP read without synthesis.
- [x] Run `.venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_realtime_cues.py` and observe the missing-feature failures.
- [x] Implement the cache and route; use temporary sibling files plus atomic replace, no dependencies beyond stdlib/existing stack.
- [x] Re-run those tests plus existing web tests affected by the route. Report commands/counts and cache failure behavior.
- [x] Commit only this task's paths and write its report.

### Task 2: Realtime activity, playback ownership, and bounded visual attention

**Files:** `src/richard/engine.py`, `conversation.py`, `realtime/{session,events,server,registry}.py`, `perception/{pipeline,camera,frames}.py`, `cli.py`, associated backend tests, `docs/realtime-api.md`.

**Interfaces consumed:** Task 1 bank is served over HTTP; the server does not play or synthesize cues.

**Interfaces produced:** Keep existing event names, add optional metadata to `response.created` and a new factual activity event:

```json
{"type":"response.created","response":{"id":"resp_1","turn_id":"turn_1","unsolicited":false}}
{"type":"response.activity","response_id":"resp_1","turn_id":"turn_1","phase":"thinking","unsolicited":false}
```

Phases are `thinking`, `tool`, `vision`, `answer`, `error`. A logical turn persists across client tool continuations and changes on fresh user input/wake. `vision` means a fresh image was supplied or a camera result with an image was received; `tool` conveys no success claim. Emit activity before blocking work through an optional engine observer, preserving existing streaming outputs for other consumers. Scope callbacks to the response that owns them; cancelled/stale work cannot emit audio into a newer turn.

Negotiate additive browser capabilities in `session.update`:

```json
{"source_id":"browser-UUID","playback_ack":true,"visual_context":true}
```

Accept `playback.update` with `response_id` and `playing` boolean. Only a known response may update playback ownership. Once audio was sent to a capable client, do not consider it idle for spontaneous speech until drained/interrupted acknowledgement arrives; handle disconnect and user speech. Legacy clients continue working without acknowledgements. Pending unsolicited attention is coalesced/bounded and retried at a genuine idle boundary; it cannot fire while VAD says the user is speaking. Preserve the ability to choose silence through camera continuations.

Bind the browser's source to its session and camera provider (never mutate a shared provider for another session). The factory can clone the camera provider for each engine and pass a source-aware observation callback into the session. Extend snapshot selection with an optional source ID while preserving legacy selection. Source IDs must be validated, bounded, and consistent across frame POST, session metadata, and event routing. Perception events carry source identity to the registry; route only to the matching opted-in session, retaining legacy behavior for clients without a binding.

At an eligible spoken/typed/unsolicited turn, provide at most one fresh low-detail source image with capture age and an explicit observation-not-description instruction. Keep it as replaceable working context (`Conversation` observation field or equivalent), not an append-only image message each turn. Clear it when stale/unavailable. Keep intentional user/tool image exchanges intact. No continuous model calls: existing scene-change events become opportunities only for opted-in visual sessions, subject to existing gate plus bounded pending attention. Departures/staleness must stop claims of current presence. Scope this to current observation, not long-term memory redesign.

- [x] Add failing real-Engine/Session tests for activity timing around blocking tools, image/error phases, logical continuation identity, and stale response suppression.
- [x] Add failing tests for wake during speech, playback draining, deferred attention firing once, stale acknowledgements, and legacy clients.
- [x] Add failing tests for source isolation, fresh/stale observation replacement, unchanged persistent history length, and matching scene-change routing.
- [x] Implement the interfaces and factory wiring with explicit ownership. Avoid additional inference concurrency and do not clear a cancellation token belonging to a different turn.
- [x] Run the affected engine, realtime, perception, and serve-assembly tests; update protocol docs with the exact implemented payloads.
- [x] Commit task paths and report any interface adjustments before frontend integration.

### Task 3: Browser cue scheduling, playback tracking, and one camera stream

**Files:** Create `src/richard/web/realtime_client.js` as a testable plain browser script and Node tests `tests/browser/realtime_client.test.cjs`; modify `web/{static,app}.py`, package data in `pyproject.toml`, relevant HTTP/browser tests and docs.

**Interfaces consumed:** Task 1 cue envelope and Task 2 negotiated source/activity/playback contracts. Update exact event spellings if Task 2 documents a necessary compatible adjustment.

Extract only the new scheduling/ownership logic into the module; keep existing SPA entry points. Serve `/realtime-client.js` using package resources and include the script before the SPA's main script. Export the controller to a browser global and CommonJS for Node tests, without a frontend framework or build dependency.

The controller receives the audio clock/scheduling hooks, loaded clip bank, and send callback. Use response/turn IDs to reject late audio/activity. Schedule a cue after 1200 ms of audible silence while an eligible solicited response is pending. `tool` uses a generic thinking clip; `vision` can use visual clips. Cancel timers and fade/stop cue audio when real audio or user speech arrives. Enforce one per gap, two per logical turn, eight-second spacing across the session, and no immediate repeated variant. Keep cues outside `replyLine`, chat history, and memory. Errors/disconnection invalidate scheduled clips. Browser playback completion sends the response acknowledgement even after `response.done`; playback and server completion have separate state.

Preload and decode the HTTP bank outside turn playback; recheck the bank identity on new sessions and after voice settings are saved so a changed voice never plays old clips. Missing cache does not block voice mode. Prepared clips should be used in their original sample rate. Never use browser speech synthesis as a fallback.

Share the same video track between voice mode and ambient perception when both use the camera, preserving separate ownership of microphone and camera lifetimes. Generate a bounded per-page source ID with `crypto.randomUUID()` (compatible fallback allowed); POST frames with it and negotiate that ID. Allow only one outstanding frame upload, send a fresh frame on session start, and connect lifecycle cleanup/visibility to the same source. Stopping voice must not stop an independently enabled ambient stream, and camera permission failure must retain voice-only operation.

- [x] Write Node behavioral tests using fake clocks and audio boundaries: fast/slow answer, two-stage tool continuation, silence/cooldown limits, interruptions, delayed old response, real-audio priority, end/disconnect, and bank change.
- [x] Run `node --test tests/browser/realtime_client.test.cjs` and observe failure before adding the controller.
- [x] Implement controller and SPA wiring; test served asset and package inclusion, not source-string mirrors.
- [x] Add a browser integration test/harness exercising actual handlers with controlled WebSocket/audio/camera boundaries: camera sharing, source identity, one upload at a time, and playback acknowledgement.
- [x] Run full Python suite and Node/browser tests; review the complete branch before deployment. Update user docs with cue preparation, language behavior, and current-vision bounds.
- [x] Commit task paths and report all checks. Do not claim auditory quality from fake-audio tests.

## Completion

- [x] Review all three tasks for spec compliance and code quality; fix substantive findings and run covering tests.
- [x] Verify the installed package contains the browser module and that missing cues degrade cleanly.
- [x] Prepare real clips on Richard's existing TTS, record the voice fingerprint and technical audio checks, and export listening samples without sending a production LLM prompt. Matteo owns the listening and conversational evaluation.
- [x] Deploy through the existing fast-forward branch workflow if covered by session authorization, verify service/web/realtime, and record the operational changelog. Preserve Matteo's ownership of live conversational testing.

Completed and deployed on 2026-09-11 at `a169eb8`. Final checks: 832 Python tests,
30 Node tests, scoped review approved, actual Chrome synthetic timing/camera
checks passed; live web/module/cue HTTP 200 and realtime session.created verified.
See `../reports/2026-09-10-browser-presence-verification.md` for evidence and limits.
