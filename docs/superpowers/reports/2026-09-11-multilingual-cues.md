# Multilingual waiting phrase preparation

Status: deployed and technically verified at `b8d9a54` on 2026-09-11. Independent
review is complete with no open findings. Matteo owns listening and conversational
evaluation.

Matteo approved separate cached groups for language and voice, automatic preparation
after settings changes, detected spoken-language selection in auto mode, and a
button to regenerate them. This extends existing flows; no separate architecture
or memory redesign is involved.

## Implementation

- Voice settings expose **Regenerate waiting phrases** and progress/error/readiness.
- Settings changes schedule missing groups; the button forces replacement even
  when a reference voice was replaced under the same ID. One worker coalesces
  duplicates and retains only the latest pending request.
- Preparation waits between clips while realtime voice sessions are open. The UI
  explains the queued state; an already-entered TTS call can finish.
- English and Italian have separate atomic fingerprinted files, with at most eight
  recently prepared variants retained. Auto prepares both. Synthesis uses each
  catalog's language without changing saved settings. Failed replacement keeps
  the last valid group. Reads never trigger synthesis.
- Final speech recognition returns per-decode language metadata, avoiding shared
  last-language state between sessions. Realtime responses carry language and
  runtime voice identity; unknown/unsupported language or incompatible runtime
  voice means no waiting cue. Typed turns may reuse the last spoken language.
- Polling uses a status-only endpoint; completed regeneration reloads audio even
  when the fingerprint has not changed. File edits require a refresh and explicit
  preparation when missing. Runtime voice/effect changes still require a restart.

## Verification

Observed RED: five preparation/cache failures; two STT/session metadata failures;
two actual SPA-handler failures; one cache-retention failure. GREEN: 222 affected
Python tests; complete suite 841 passed, 2 skipped, 3 deselected in 8.94 seconds;
32 Node tests passed. Diff whitespace clean, commit `67d6663`.

Independent reviewer `/root/review_multilingual_cues` (Sol high) identified four
issues: unsupported language did not supersede an active preparation, the waiting
section was absent from the Voice drawer mount list, a CLI repair left stale error
status, and HTTP 202 used the wrong reason phrase. All four were fixed in
`b8d9a54`; scoped re-review returned Ready with no critical, important or minor
findings. Two new regressions were observed failing before the queue/status fixes.
The existing Voice layout assertion also detected the changed drawer contents and
was updated to require the new section.

Final suite: **843 passed, 2 skipped, 3 deselected in 9.55 seconds**; **32 Node tests
passed**. Diff whitespace clean. Failed forced replacement remains visible with
old valid audio; a subsequently published valid cache clears that error. An
unsupported-language change cancels the old job after any already-entered clip.

## Chrome acceptance

The existing synthetic fixture gained `--real-cues`, using real WebApp config,
cache and background-job handlers with only TTS/media boundaries replaced. A
real Chrome run found a missing `voice-cues-content` entry in the drawer mount
list; the one-line fix is committed in `b8d9a54`. With that correction, the block
appears under Configuration → Voice and not on the home page.

The button disabled during preparation and reached Ready12 EN/IT. Saving Italian
reused its existing six clips. A manual forced request with synthetic voice mode
open stayed queued0/6, then progressed2/6 after voice closed and reached Ready6 IT.
Saving auto reused both banks and returned Ready12 EN/IT. Actual-handler Node
tests verify per-response language and runtime voice matching; Chrome verified
the real settings/job/cache flow without real inference or TTS.

Raw observations: `results/2026-09-11-multilingual-cues-chrome.txt`.
SPA SHA256: `af14c7468d0b9ddd3165b49e0b355277d87d2bad2aa2ae9171459c29b837fa8b`.
The browser was returned to about:blank and the exact fixture process stopped.

## Deployment

Public `reachy-presence` and the box's `/opt/richard` fast-forwarded from `a169eb8` to
`b8d9a54c5d85cc53a354f7b79943ca7479338c3e`, with clean checkouts. Public outgoing
tree and diff scans passed. Richard restarted at **08:05:33 UTC**; Qwen3-TTS
retained its **2026-09-08 14:41:10 UTC** start timestamp.

The new preparation endpoint returned **202 Accepted**, then progressed from
queued to preparing to **ready, 12/12**. Both language banks were generated using
the existing TTS service. Web, browser module, cache and status routes returned
HTTP 200 with `Cache-Control: no-store`; the served module matched installed bytes
(`f77aea4bd70e7e00193a5bb4d6a8576323be0a9264115f8897fdc900447e555d`).
Deployment verification received `session.created` and closed without a prompt.

Preflight and post-deploy configuration hashes match:
`45814ccf9bb27920b015a6b2cd31ad528c64bc82f193077b7a1439b75a8f7c61`.
The live configuration at this deployment used voice **richard**, language **auto**,
robot effect strength **20**, tone **40**. This differs from the earlier milestone's
archived voice name; the current settings were preserved. Live banks also matched
fingerprints computed directly from that configuration.

- English fingerprint: `aa7f8d10d077ce1b680e80029312d494cbc46594ec1cd59c76695230533b5983`.
  Six non-silent mono PCM16 clips at 24 kHz, 1.04–2.56 seconds.
- Italian fingerprint: `e173b91c5c8bfecf50f280615325495c831bf73215e88a2459e3a6456d308885`.
  Six non-silent mono PCM16 clips at 24 kHz, 1.28–2.72 seconds.

WAVs and per-clip technical metrics are under
`samples/multilingual-cues-2026-09-11/{en,it}/`. These checks establish valid audio,
not pronunciation or perceived naturalness. No production LLM prompt was sent;
The LLM box's configuration, resident model and power caps were unchanged.

Reload the browser to use **Configuration → Voice → Regenerate waiting phrases**.
Saving relevant settings schedules missing banks; forcing regeneration replaces
audio even when the reference file changed under the same voice ID. Preparation
queues while voice mode is open and resumes when it closes. Vector retrieval is
still paused.

The box's changelog and current configuration snapshot were pulled and archived in
a private box-docs commit `46fc611`, pushed on `software-headroom-20260908`. The only
configuration snapshot difference was the voice name already present at preflight;
this deployment made no configuration edit. Final passive box status showed
production Qwen ready. Unrelated untracked files were preserved, personal memory
was updated, and coordination entry C069 released the shared index window.
