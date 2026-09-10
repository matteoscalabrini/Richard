# Task 3 report — browser cue playback and shared presence media

## Status

Implemented and locally verified. The browser now schedules only prepared same-voice
waiting cues, owns response playback through actual drain, shares one camera track
between voice and ambient perception, binds that camera to one bounded page source,
and routes composer submissions through the active realtime session. No production
service, real microphone/camera, push, or deployment was used.

## Implemented interfaces and behavior

- `src/richard/web/realtime_client.js` is a dependency-free UMD script. It exports
  `RealtimePresenceController`, `SharedCamera`, `FrameUploader`, and
  `createBrowserSourceId` through both `globalThis.RichardRealtimeClient` and
  CommonJS.
- `RealtimePresenceController` consumes injected clock, clip playback/stop, random,
  and send hooks. Its public protocol boundary is `setBank`, `responseCreated`,
  `activity`, `accepts`, `realAudioScheduled`, `realAudioEnded`,
  `realAudioFlushed`, `responseDone`, `speechStarted`, `protocolError`, and
  `disconnect`.
- Eligible solicited `thinking`/`tool` activity selects the prepared `thinking`
  catalog; `vision` selects `visual`. A cue starts after 1200 ms of audible silence,
  at most once per audible gap and twice per logical turn, with at least 8000 ms
  between cue starts across the page session and no immediately repeated variant.
  Activity is idempotent. Unsolicited turns, missing banks, errors, terminal events,
  disconnection, speech, and answer PCM remain silent or fade/stop the cue.
- Response and turn ownership rejects late activity, text, function calls, audio,
  truncation, and terminal events. The backend's metadata-free empty response stays
  trackable only through its terminal boundary and is never cue-eligible.
- Real PCM sends `playback.update playing:true` once per owned response. The matching
  `playing:false` is sent only after `response.done` and every scheduled source has
  ended, or immediately when playback is flushed/interrupted. Server completion and
  browser drain are separate states. Recoverable error activity invalidates cues but
  does not retire the response, so later owned answer PCM still plays.
- Cue PCM is decoded during bank fetch, outside turn playback, and Web Audio buffers
  use each clip's stored sample rate. Cue sources use a gain node and a 40 ms ramp/
  50 ms stop on interruption. Cue text never enters `replyLine`, `chatHistory`, or
  memory, and there is no speech-synthesis fallback.
- The cue endpoint is re-read during initial page load, before every new voice
  session, and after any voice settings section is saved. A changed, removed,
  malformed, or wrong-voice bank replaces the old bank with silence and stops an
  active old-bank cue.
- `SharedCamera` coalesces concurrent `voice` and `ambient` acquisitions into one
  `getUserMedia({video: ...})` request and one video track. Generation tokens prevent
  an unresolved acquisition from resurrecting a released owner. The track stops and
  the hidden video is removed only after the final owner releases it. Voice obtains
  its microphone separately, so camera denial keeps voice operational and stopping
  voice does not stop ambient video.
- One `browser-<UUID>` source is created per page (64-character server bound and a
  compatible random fallback). The same ID is negotiated in `session.update` and
  posted with ambient frames. `FrameUploader` permits one request in flight, drops
  redundant periodic ticks, and queues one forced fresh capture after an earlier
  upload drains. A session start requests that fresh capture. Visibility, voice/
  ambient stops, and `pagehide` release the appropriate owners on the same page
  source.
- While voice mode is active, typed text and attached images interrupt local audio
  and send the usual realtime `conversation.item.create` plus `response.create` on
  that websocket. With voice mode off, the existing `/api/chat` SSE path is unchanged.
- `/realtime-client.js` is served from `importlib.resources` as no-store JavaScript,
  loaded before the inline SPA handler, and included in built wheels through package
  data. User docs describe cue preparation/language/fallback behavior, camera sharing,
  source/upload bounds, and the 800/1600-pixel vision limits.

## TDD evidence

### Controller RED

The complete behavioral test was written before the controller existed:

```text
$ node --test tests/browser/realtime_client.test.cjs
Error: Cannot find module '../../src/richard/web/realtime_client.js'
✖ tests/browser/realtime_client.test.cjs
ℹ tests 1
ℹ pass 0
ℹ fail 1
```

The failure was expected because the required production module did not exist.
After the initial implementation, the focused controller suite reached 14/14.

Additional focused RED cycles found four boundary defects during implementation:

```text
$ node --test --test-name-pattern='same fingerprint' tests/browser/realtime_client.test.cjs
✖ rechecking the same fingerprint with a now-missing bank stops its cue
ℹ tests 1
ℹ pass 0
ℹ fail 1

$ node --test --test-name-pattern='speech releases|reports a failed forced' tests/browser/realtime_client.test.cjs
✖ speech releases acknowledged playback even when its last source already ended
✖ frame uploader reports a failed forced follow-up without an unhandled rejection
ℹ tests 2
ℹ pass 0
ℹ fail 2

$ node --test --test-name-pattern='empty protocol' tests/browser/realtime_client.test.cjs
✖ an empty protocol response without turn metadata still reaches its terminal boundary
ℹ tests 1
ℹ pass 0
ℹ fail 1
```

Each failed on the missing production behavior named by the test. The fixes stop a
now-invalid same-fingerprint cue bank, release acknowledged playback on speech even
after natural source end, catch the queued forced-upload rejection, and preserve the
backend's empty-response terminal behavior.

### Asset/wheel RED and GREEN

```text
$ .venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_web_assets.py
FFF                                                                      [100%]
3 failed in 0.13s
```

The asset route was 404 and the external script tag was absent, as expected. The
third initial failure also exposed a test-harness issue: this uv-created venv has no
`pip` module. The test was corrected to build the actual wheel with `uv build`; it
then exercised wheel contents rather than configuration text.

```text
$ .venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_web_assets.py
...                                                                      [100%]
3 passed in 0.46s
```

### Actual SPA-handler RED and GREEN

The integration suite loads the rendered `SPA_HTML`, executes its real inline
handler in a VM, and controls its DOM, WebSocket, Web Audio, fetch, and media
boundaries. It does not assert mirrored source text.

```text
$ node --test tests/browser/spa_integration.test.cjs
✖ actual ambient and voice handlers share one camera and negotiate the page source
✖ actual session handler queues a fresh frame behind an existing upload
✖ actual response handler acknowledges playback only after done and audio drain
✖ typed text and images use the active realtime session and interrupt local playback
ℹ tests 4
ℹ pass 0
ℹ fail 4
```

All four failed at the expected missing integration boundary (`ReferenceError:
sharedCamera is not defined`). After wiring:

```text
$ node --test tests/browser/spa_integration.test.cjs
✔ actual ambient and voice handlers share one camera and negotiate the page source
✔ actual session handler queues a fresh frame behind an existing upload
✔ actual response handler acknowledges playback only after done and audio drain
✔ typed text and images use the active realtime session and interrupt local playback
ℹ tests 4
ℹ pass 4
ℹ fail 0
```

Two further actual-handler cases were added for stale text/tool/truncation/done
filtering and camera-denied voice-only operation; the final integration count is 6.

## Final verification

Focused web and packaging regression run:

```text
$ .venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_web.py tests/test_web_assets.py
100 passed in 0.77s
```

Final Node controller plus actual-handler integration run:

```text
$ node --test tests/browser/*.test.cjs
ℹ tests 24
ℹ pass 24
ℹ fail 0
ℹ duration_ms 533.566125
```

Final full Python suite after production edits:

```text
$ .venv/bin/python -B -m pytest -q -p no:cacheprovider
829 passed, 2 skipped, 3 deselected in 8.72s
```

Final wheel/asset check after the test assertion was tightened to compare the served
resource bytes:

```text
$ .venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_web_assets.py
3 passed in 0.46s
```

`git diff --check` produced no output.

## Files changed

- `src/richard/web/realtime_client.js`
- `src/richard/web/static.py`
- `src/richard/web/app.py`
- `pyproject.toml`
- `tests/browser/realtime_client.test.cjs`
- `tests/browser/spa_integration.test.cjs`
- `tests/test_web_assets.py`
- `docs/realtime-api.md`
- `README.md`
- `.superpowers/sdd/2026-09-10-browser-presence/task-3-report.md`

## Self-review findings

- Replaced the initial package test's unavailable `python -m pip` invocation with an
  actual isolated `uv build` and wheel ZIP inspection.
- Removed a source-token assertion from the asset route test; it now compares the
  served bytes with the installed package resource.
- Added the same-fingerprint missing-bank case so a deleted cache cannot finish an
  already-started prepared cue after a recheck.
- Added speech release for acknowledged playback whose final browser source ended
  before `response.done`; this avoids holding backend playback ownership during a
  new utterance.
- Added explicit handling for a failed forced follow-up upload, avoiding an unhandled
  browser promise rejection.
- Preserved metadata-free empty responses after checking the finalized backend event
  builder, without making them eligible for cues.
- Confirmed old terminal/truncation events cannot flush or finalize newer response
  state, and recoverable error activity still permits later answer PCM.

## Limits and concerns

- Fake-clock and fake-audio tests establish scheduling, fade/stop calls, PCM sample
  rate selection, and playback ownership boundaries. They do not establish auditory
  quality, loudness, pronunciation, or echo-cancellation behavior.
- The real-browser fixture and listening/timing check are owned by the controller and
  remain outside this task's repository paths. Prepared clips were not synthesized
  and no production LLM prompt or service was used here.
