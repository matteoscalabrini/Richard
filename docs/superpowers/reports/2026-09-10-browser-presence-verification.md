# Browser presence verification

Status: deployed and technically verified on 2026-09-11 at `a169eb8`. All task and
final scoped reviews passed. This report distinguishes local synthetic
verification from production deployment and Matteo's conversational evaluation.

## Scope

Prepared waiting speech, playback-aware turn ownership, and fresh source-bound
visual context for Richard's browser UI. The vector database track remains paused.
The physical Reachy app and SDK are outside this change.

## Baseline

- Richard worktree: `reachy-presence`, baseline `0577aff`.
- Fresh baseline suite: 769 passed, 2 skipped, 3 deselected.
- CT123 still served `0577aff` during read-only preparation; both Richard and TTS
  services were active. No inference prompt was sent.
- The actual baseline SPA was opened in Chrome against the local synthetic
  fixture. Ambient perception opened one video capture. Starting hands-free mode
  opened another combined audio/video capture. The fixture observed two live
  video tracks and one live audio track, demonstrating the duplicate-camera path.
- With frame replies delayed 650 ms against the configured 500 ms interval, the
  baseline fixture recorded 50 uploads and a maximum of two concurrent uploads.
  All used the global source `browser`; no session source binding or playback
  acknowledgement was negotiated.

## Local fixture

`scripts/browser_presence_fixture.py` serves the real installed Richard SPA and
realtime WebSocket transport against a synthetic session boundary. Its companion
JavaScript supplies synthetic media streams, silent PCM, visible event timestamps,
and controls for slow/fast responses, interruption, and scene changes. It never
opens the real microphone/camera or contacts an external model/TTS endpoint.

The fixture deliberately delays frame uploads and sends the response completion
event before all queued audio has finished. This makes source sharing, upload
backpressure, and playback acknowledgements observable independently of inference.

## Prepared cue cache

- Commit: `001f9d2`.
- Implementer evidence: missing-module RED, overlong-PCM RED, then 32 passing cue
  tests and 129 passing cache/web tests. Full suite: 801 passed, 2 skipped,
  3 deselected.
- Independent task review: spec compliant and approved; no blocking findings.
- Integration note: activity phase `vision` maps to cue-bank phase `visual`.
  The browser integration must cover this mapping behaviorally.

## User evaluation

Implementation, review, deployment and technical audio checks are complete.
Matteo owns live conversational testing and the listening evaluation; synthetic
timing and audio measurements do not establish subjective naturalness.

## Realtime backend

- Implementation commit: `999f0b9`; independent task review completed with the
  corrective commit below.
- Review fixes: `79448bf`. The review found explicit cancellation during blocked
  STT could still enter inference, and TTS errors omitted scoped activity. Both
  defects were reproduced, fixed, and passed 73 covering tests. Scoped re-review
  approved both fixes and adjacent answer-phase deduplication without new findings.
- Real Engine/Session tests reproduce and then cover blocked tool replacement,
  stale STT delivery, activity phases, playback acknowledgement, VAD idle gating,
  logical camera continuations, and source routing.
- Complete suite at `999f0b9`: 825 passed, 2 skipped, 3 deselected in 8.60 seconds.
  The subsequent review fix passed the 73 affected tests; the final branch suite
  will include that fix and the browser integration.
- Dynamic browser sources retire after `max(30 seconds, 4 * stale_s)` without
  another request being needed. Frame freshness and current presence stop at
  `stale_s`, independently of that longer resource-retirement window.
- An already-entered external call finishes on the single response worker. Its
  obsolete speech is suppressed; a tool's factual result remains in order before
  queued new input.

## Fixture transport check before frontend changes

The actual legacy SPA successfully completed a client camera call through the new
WebSocket transport and synthetic session. Responses `resp_fixture_1` and
`resp_fixture_2` kept `turn_fixture_1`; the continuation received the captured
image before its `vision` phase. The synthetic response completed at page time
9.887 s, while its last audio ended at 10.930 s, demonstrating the playout gap the
new browser controller must track. These timings validate the fixture, not
production latency or auditory quality.

## Active voice and typed input

Typed input and attached images use the existing realtime session while voice
mode is active. This gives them the same cancellation and visual-source ownership
as microphone input and avoids starting an independent HTTP inference request.
Replies to typed input in this mode are spoken. With voice mode off, the existing
HTTP text-chat path remains available.

This was the sole implementation ruling. Its observable cost is spoken replies
to typed input while voice mode is active; the routing can be reverted independently.

## Chrome integration check

The actual SPA was driven in Chrome through its visible controls against the
local synthetic fixture. Raw visible protocol/audio observations are saved in
`results/2026-09-10-browser-presence-chrome-1.txt` and `-2.txt`.

- Ambient and voice mode shared one video track; voice added one separate audio
  track. Stopping voice left the ambient video track alive.
- All 61 observed frame uploads used the session's exact per-page source ID;
  maximum concurrent uploads was one despite the fixture's slow HTTP replies.
- A camera call and its image continuation retained `turn_fixture_1`. The visual
  activity followed receipt of the image. A cue started 1.203 seconds after the
  initial wait began.
- Typed `fast` in voice mode received a spoken realtime answer in approximately
  151 ms at the synthetic boundary, without a cue.
- An answer received during a cue stopped it in approximately 1 ms; its short fade
  ended around 49 ms later. Simulated user speech stopped its cue in the same
  millisecond at the fixture's timestamp resolution; fade ended 50 ms later.
- For the camera continuation, server completion occurred at page time 14.452 s;
  the final audio drain and `playing:false` acknowledgement occurred at 15.499 s.
- Waiting text did not replace the visible answer. The page was returned to
  `about:blank` after verification, releasing its synthetic devices.

These are controlled browser timings, not production inference/TTS latency or a
listening evaluation. The first run's files are identified below; subsequent
changes received the scoped repeat described next.

```
static.py       f63c3c87335d6cc4e7236cfd2fd61645ea6b57fc00c2785cfd267fb361a004b3
realtime_client.js efb5a33be0b4b60fc582e134149444c249690a53c73cce709f967823ccfdde06
```

## Final client verification

- Implementation commit: `36b7ba4`. Complete Python suite: 829 passed, 2 skipped,
  3 deselected in 8.72 s. Browser controller and actual-handler tests: 24 passed.
  The 3 asset tests build a wheel and verify the packaged/served script bytes.
- The final fixes cover page closure, failed forced uploads, released playback
  acknowledgement after an early source end, and metadata-free empty responses.
- A scoped Chrome repeat on the committed files confirmed an empty response
  returned to READY, then a fresh slow response emitted a cue after 1.202 s and
  acknowledged playback only after the last audio source. One shared video track,
  one microphone, matching source identity and maximum one concurrent frame upload
  were retained. Navigating away closed the session; frame count remained at 31
  on two subsequent status reads, confirming uploads had stopped.
- Raw final-head evidence: `results/2026-09-10-browser-presence-chrome-final.txt`.

```
static.py       dc7c4e5a2fd95edba1165a52fc7add6c45ad37e4c127a3b0f31c371cab223edb
realtime_client.js 22364f3781cbfd8b0d9c3f44f476318ded7b76f887ec1270989eb5d8023854ac
```

## Client review corrections

Independent review of `36b7ba4` reproduced three Important defects that normal
browser flows did not expose:

1. A later tool/vision phase could start a cue over queued real audio, or before
   1.2 seconds of silence after its drain.
2. Speech/typed interruption retained the old response and pending camera call,
   allowing stale events to produce ghost output or a camera request.
3. Stop/restart during unresolved camera permission let stale cleanup release the
   replacement camera owner and retain the old WebSocket.

The implementer reproduced all three in focused Node tests before fixing them.
The local Chrome fixture now also supports late events after interruption and
delayed camera permission (`--no-ambient`, page query `cameraDelay=4000`) for the
post-fix browser check. A separate Minor finding tracks retained terminal/drained
playback entries; the whole-branch review will triage it against the runtime bound.

Fix commit `6f2747b` passed 28 Node and 100 covering Python tests. The added tests
include PCM still queued from the prior response during a camera continuation;
an active-response-only mutation failed that regression before restoring the
all-playback guard. Independent scoped re-review confirmed all three findings
addressed with no new breakage. The playback-map Minor remains for final triage.

Chrome on `6f2747b` confirmed late text, PCM and terminal events after speech
interruption produced no stale answer or camera continuation. The cue stopped at
the speech event (43.704 s), ending its fade 52 ms later. Raw evidence is in
`results/2026-09-10-browser-presence-chrome-fix.txt`.

The real browser disables the voice button while startup is pending, so a rapid
same-page stop/restart ordering is established by the actual-handler Node test,
not claimed as a successful UI click sequence. A separate Chrome navigation
during a four-second delayed camera startup closed both the prior and pending
sessions (2 created / 2 closed) and left no active WebSocket session.

```
static.py       273e58241591f3726b87273b707428c14e3ed513fd95be6336bf1ab756735b82
realtime_client.js e51b50117ae71e4e90928db511027413ff92ff46c059cf4744b5084a07b0984f
```

## Whole-branch review

The final review confirmed that cancellation could start a server tool and another
brain round after a blocked tool-only completion returned. It also reproduced a
queued typed/image input being stranded when the old worker exited before
`response.create`. The fix must stop future work at those boundaries while keeping
the factual result of an already-entered tool.

The same review requires `Cache-Control: no-store` for the cue endpoint and records
two small page-lifetime map leaks: completed playbacks and old unique camera-owner
generations. One final fix wave covers all four findings, followed by complete
Python/Node verification and a scoped re-review.

The final fix commit `a169eb8` reproduced four Python and four Node failures
before changing production code. All covering tests passed afterward: 147 Python
and 30 Node. The full final suite passed with 832 Python tests, 2 skipped and
3 deselected; all 30 Node tests passed. The outgoing tree's required private-name
scan, added-line sensitive-pattern scan and diff whitespace check were clean.

The cancellation predicate now prevents starting later tools or inference after
cancellation. An already-entered tool keeps its factual result; unrun calls are
explicitly marked cancelled. Queued input is scheduled even after the old worker
has exited. Both cue HTTP responses and browser fetches prohibit caching.
Completed/drained playback records and released camera acquisition identities
are removed without losing drain acknowledgements or stale-callback protection.

## Final Chrome check on 2026-09-11

The actual SPA at `a169eb8` passed the queued-audio/tool case: four answer chunks
finished at 12.711 s, the waiting cue began at 13.913 s (1.202 s of real silence),
and subsequent answer activity at 14.666 s stopped the cue at 14.667 s. Its fade
ended 50 ms later. Final playback acknowledgement followed the final answer drain
at 15.714 s, after server completion at 14.668 s.

A following client camera call retained its logical turn through image delivery
and the vision phase. The page retained one video and one microphone, a matching
frame/session source, and at most one concurrent upload. Stopping voice released
the microphone and retained ambient video. Navigating away closed the session;
the last in-flight upload completed, then frame count remained 73 on two reads.
The fixture was stopped and the browser left at `about:blank`.

Raw evidence: `results/2026-09-11-browser-presence-chrome.txt` and
`results/2026-09-11-browser-presence-chrome-lifecycle.txt`. Browser logs also
reported three asynchronous message-channel closure errors; no tested Richard
flow failed, and these logs are preserved rather than claimed clean. This check
uses synthetic audio and makes no naturalness or production latency claim.

```
static.py       54829a8b04db1498e31e684967cd779c22358e23cd25541ab975056ee5a881ae
realtime_client.js f77aea4bd70e7e00193a5bb4d6a8576323be0a9264115f8897fdc900447e555d
```

Final scoped re-review approved all four findings at `a169eb8`, with no new
breakage. Evidence: Engine cancellation boundaries at engine.py:129 and :220,
session callback/sealing at session.py:552 and :643; queued input at session.py:213;
no-store response/fetch at web/app.py:723 and web/static.py:1602; playback cleanup
and camera acquisition ownership at realtime_client.js:139 and :266. No further
test was required by the reviewer. Task and final review ledgers are complete.

## Deployment on 2026-09-11

- Public `reachy-presence` and CT123 `/opt/richard` fast-forwarded from `0577aff`
  to `a169eb813530f336131fb1453728205f1ca59801`; both checkouts clean.
- Richard restarted at **06:58:20 UTC**. The Qwen3-TTS service retained its
  original active timestamp, **2026-09-08 14:41:10 UTC**; no restart was needed.
- Configuration preserved: `language=auto`, `tts_engine=remote`,
  `tts_voice=clap1v`, `tts_effect=robot`, strength **20**, tone **40**.
- Explicit cue preparation produced six English clips. The live endpoint
  returned all six with `Cache-Control: no-store`; the browser module returned
  matching installed bytes and the same no-store policy. Web HTTP 200 was also
  confirmed from the Mac. Deployment verification received `session.created`.
- Cue fingerprint: `0c8110d949b941cf82d0e2af1a5ab7b44dcf3783e9d6e1e6a314c1612793ddd1`.
  All six exported WAVs are non-silent mono PCM16 at 24 kHz, 1.20–2.48 seconds;
  measured peaks 0.301–0.459 of full scale. Files and per-clip metrics are in
  `samples/browser-presence-2026-09-11/summary.json` and the adjacent WAV files.
- No production LLM prompt was sent. The live model, TTS settings and power caps
  were preserved; physical Reachy and vector retrieval remain outside this work.
- Restart/reload creates a fresh browser session. Voice-active typed input uses
  that same session and its replies are spoken, as recorded in the ruling above.
- The CT123 dated changelog was pulled and archived in inference-box commit
  `e43dc60`, pushed on `software-headroom-20260908`. Personal deployment memory and
  coordination were updated; the shared index window is released. Final passive
  box status showed the production model ready, with existing services resident.
