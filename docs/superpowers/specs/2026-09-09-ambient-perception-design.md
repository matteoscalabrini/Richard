# Ambient perception: sensor, gate, brain — design (spec 3b)

Status: direction approved by Matteo 2026-09-09. His framing: a vision layer "less based on
snapshots and photographs and more on how human vision works: an always-present layer that
monitors, recognises events and passes them, if judged important, to the LLM, the brain, which
wakes up and requests snapshots to work out in more detail what the camera sees"; the brain must be
able to ask for higher-resolution frames when it judges it necessary. Confirmed after discussion:
three stages, not two (the cheap layer cannot judge importance; a deterministic gate filters
repetition, the brain filters relevance); the layer consumes video, the brain consumes snapshots;
the layer runs on the box as a Richard plugin, fed first by the browser webcam, then by the robot;
face identity is included, opt-in. Depends on spec 3a (the fovea: multimodal messages, client
tools, image items). Wake-ups need spec one (server-initiated turns, session registry); durable
observations need spec two (memory provenance); when to speak is spec four (initiative).

## Why three stages

- The brain cannot watch video. One 800x500 frame is 474 prompt tokens, a 720p frame ~1200. At one
  frame per second the box would do nothing else, and almost every frame says "nothing changed".
  Human vision has the same economy: low-resolution motion-sensitive periphery, attention that
  selects, a fixation of 200-300 ms on the detail. The fixation is a snapshot. What changes with
  this spec is who presses the shutter: the ambient layer, through the brain, instead of the user.
- Importance is contextual. Whether "Matteo entered" deserves a word depends on whether a
  conversation is running, whether he was greeted three minutes ago, on the hour. Only the brain
  holds that context. A rule-only judge is the intrusive doorbell `PHILOSOPHY_REACHY.md` warns
  against; an LLM judge on every frame is the token bill above. So: the **sensor** produces
  structured facts at near-zero cost, the **gate** removes repetition deterministically (debounce,
  transitions only, cooldowns, quiet hours), the **brain** decides relevance and action (look
  closer, speak, remember, stay silent).
- Initiative (spec four) is impossible without this. Richard cannot greet someone who walks in
  unless something is watching.

## Facts that decide the design

- The Reachy daemon's media server "owns the physical camera and audio hardware and distributes
  media to consumers" over two channels: IPC (`unixfdsink`) for on-device apps, WebRTC for remote
  clients including the Python SDK (`ReachyMini(media_backend="webrtc", signalling_host=<robot>)`).
  The stock Conversation App on the Pi is an IPC consumer; Richard on the box is a WebRTC consumer.
  The single-consumer limit documented for the HF central relay does not apply on the LAN.
  **Unverified on the Wireless hardware: two consumers at once.** First thing to test when the
  robot arrives. Fallback if it fails: a small separate app on the Pi that publishes only events
  and frames on request to Richard; still no fork of the Conversation App.
- Bandwidth: 720p WebRTC from the robot ~1.5 Mbps; the browser stream below ~1.2 Mbps. Trivial on
  the LAN.
- Richard's core has no image codec (numpy only) and `onnxruntime` is already a dependency of the
  `voice` extra (Silero VAD). This spec adds a `perception` extra: `Pillow` (decode, resize, crop)
  and `onnxruntime`; models are fetched on first use like Silero (`ensure_*`). GPU1 on CT123 has
  about 12 GB free; CPU first (16 cores), GPU when a measurement says so.
- Detector costs (class figures, to be measured in the plan): frame differencing on a 160x90 grey
  image is microseconds; a nano person detector at 320 px is 30-60 ms on CPU, ~5 ms on GPU; a face
  embedding is 10-20 ms per face and runs only on new-person events.
- The engine's client-tool mechanism (spec 3a) and a provider-owned camera share the same history
  shape: assistant tool call, tool result, user image. The brain never sees which path took the
  picture.

## Design

### 1. Placement: a built-in plugin, code in a core package

`richard.perception` holds the source-agnostic machinery (frames, sensor, events, gate, log).
`richard.plugins.perception` is a built-in plugin (entry point `perception`) so the capability is
scoped the way spec zero scopes connections: disabled means invisible to the model, no camera tool,
no events, no stream accepted. Its `PluginParts`: one event source (the gate's output), one
provider (the server-side `camera` tool and `who_is_here`), one context line ("You have ambient
perception: you are told when someone arrives or leaves and can look with `camera`"). Config under
`[plugins.perception]`.

### 2. Frame sources

- `Frame(ts, rgb: ndarray HxWx3, source_id)`; `FrameSource` protocol: `latest() -> Frame | None`,
  `native_jpeg() -> bytes | None` (the source's full-resolution frame for the high-detail path).
  A `FrameHub` keeps one pipeline per registered source; sources come and go at runtime.
- **Browser** (first source, built into the web UI): when perception is enabled and the tab is
  visible, the home page streams webcam frames at 2 fps, 800 px long edge, JPEG quality 0.7, to
  `POST /api/perception/frame` (JSON base64, same pattern as `/api/voices`). The camera indicator
  in the page stays on while streaming; the stream stops when the tab is hidden. Native = 800 px
  for this source.
- **Robot** (with the Reachy body plugin, spec four's package): `ReachyFrameSource` wraps the SDK's
  WebRTC media client on the box, `get_frame()` at 2-5 fps into the hub, `get_frame_jpeg()` as
  native (1280x720 class). Registered when the body plugin connects.
- **Files** (tests and replays): a directory of numbered JPEG/PNG frames with timestamps, played
  at a chosen rate. Deterministic tests never touch a camera.

### 3. Sensor

Per source, on every frame, cheapest first; each stage can short-circuit the next.

- `MotionDetector` (numpy only): grey 160x90, absolute difference against the previous frame and
  against a slow background; outputs an activity ratio and a scene-change flag (a large change
  that persists for 3 s: lights, a moved camera, a new arrangement).
- `PersonDetector` (ONNX, nano class, permissive licence; model pinned in the plan after a
  measurement): runs when activity is above a threshold or every Nth frame while people are
  present; outputs boxes with scores.
- `FaceIdentifier` (ONNX face detector + embedding, permissive licence, pinned in the plan): runs
  on a new person or every few seconds on a present unknown; cosine match against the local gallery
  with a threshold; below threshold → `unknown`. **Opt-in**: `identity_enabled` off by default;
  the gallery (`~/.richard/faces.db`: name, embeddings, enrolment date) is filled only from the
  Perception page (name + three webcam or robot snapshots) and any entry is deletable, which also
  removes it from the presence log's future matches. No embedding or frame ever leaves the box.
- `StillnessTracker`: minutes without activity; feeds the `stillness` event and the "is anyone
  here" state.

### 4. Events and presence

`PerceptionEvent(ts, source_id, kind, subject, confidence, box)` with kinds `person_entered`,
`person_left`, `identified` (subject = gallery name), `unknown_person`, `motion_after_stillness`,
`scene_changed`, `stillness`. `PresenceState` per source: who is present (name or `unknown`) and
since when. A `PresenceLog` (sqlite table `perception_log`) stores events with timestamps and, off
by default, a thumbnail; it answers "when did you last see me?" through the `who_is_here` /
`last_seen(name)` tools and is the raw episodic material spec two turns into memories with
provenance "observed".

### 5. Gate

`Gate(clock, policy)` turns the raw stream into the few facts the brain should hear.

- Transitions only: presence changes, identity resolved, scene change, motion after a long
  stillness. Never "person still present".
- Debounce: a person must persist ≥ 2 s to have entered and be absent ≥ 10 s to have left; an
  identity needs two agreeing matches.
- Cooldown per (kind, subject), default 120 s; quiet hours from config; a master `enabled` switch.
- Conversation-aware: when a session is active (spec one's registry), gated events are not
  separate wake-ups; they are appended as an internal user-role item `[perception] Matteo entered
  (18:42)` immediately before the next turn runs, append-only, so the brain sees them in context.
  When idle, gated events go to the sinks.
- Sinks: the control-loop monitor's event source (loops may target `perception:person_present`,
  `perception:identified:matteo`, spec zero's namespaced targets); the presence log; and, with spec
  one, `session.prompt("[perception] ...")` on the open session so the brain can speak, look, or
  answer with the silent sentinel. The policy of *when* it is right to speak is spec four; this
  spec ships the mechanism and conservative defaults.

### 6. The brain looks: server-side `camera` with detail and region

- Provider tool `camera(question, detail="low"|"high", region=None)`. Low = the latest frame at
  800 px long edge (~474 tokens). High = the source's native frame (~1200 tokens on the robot).
  `region` = a normalised box `[x0, y0, x1, y1]` or one of `left|centre|right|top|bottom`, cropped
  from the native frame and then bounded to 800 px: a saccade to the detail at full sensor
  resolution, which is how small text and small objects are read. The tool description says so
  and tells the model to start low.
- Engine: a provider's `execute` may return a `ToolResult(text, images=[data_url])`. The engine
  appends the tool message, then a user message with the image parts, exactly the app's sequence,
  so history and tests have one shape. String results keep working unchanged.
- Name collision with the app's `camera`: Richard's provider wins when a live source exists for the
  connected robot; with no live source the provider offers no `camera` schema, so the app's
  client tool (spec 3a) is used. The two paths are interchangeable for the brain.

### 7. Web UI

Perception page in the drawer: enable, identity on/off, quiet hours, sensitivity, cooldown;
source status (browser, robot); latest frame thumbnail (`GET /api/perception/latest.jpg`); live
event log (`GET /api/perception/events?since=`); gallery with enrol (name + three snapshots) and
delete. Home page: the frame streamer with a visible camera indicator. The chat log shows
`[perception]` items as small grey lines so what the brain was told is visible.

### 8. Configuration

`[plugins.perception]`: `enabled`, `identity_enabled` (default false), `quiet_hours` (e.g.
`"23:00-07:30"`), `sensitivity` (0-100, maps to the motion and detection thresholds), `cooldown_s`
(120), `enter_debounce_s` (2), `leave_debounce_s` (10), `stream_fps` (2), `keep_thumbnails`
(false), `device` (`cpu` | `cuda`).

## Numbers that bound the design

| item | figure |
|---|---|
| frame to brain, low detail | ~474 tokens |
| frame to brain, native 720p | ~1200 tokens |
| browser stream, 2 fps 800 px | ~1.2 Mbps |
| person detector, 320 px, CPU | 30-60 ms |
| face embedding, per face | 10-20 ms |
| arrival → greeting (debounce 2 s, LLM 0.3-0.7 s, optional look 1-1.5 s, TTS 0.5 s) | 3-5 s |

Good enough for a greeting; not for tracking, which stays on the robot (the app's head tracking
runs locally).

## Error handling

- A source that stops producing frames is marked stale after 5 s; presence decays to empty after
  the leave debounce; no event storm.
- Detector or model load failure disables that stage with one log line; motion and presence from
  the remaining stages keep working; the Perception page shows the stage as down.
- Bad frames on `/api/perception/frame`: 400, dropped. Oversized: 400.
- Gallery match on a corrupt embedding: treated as unknown.

## Testing

- Synthetic frames (numpy) for the motion detector and stillness tracker; fake detectors returning
  scripted boxes and embeddings; the gate with a fake clock (debounce, cooldown, quiet hours,
  transitions only, conversation-aware routing); presence state machine; the log store; the
  provider's `ToolResult` path in the engine (history shape identical to the client-tool path);
  the plugin contract (`event_sources` start/stop); web endpoints (frame in, events out, gallery
  CRUD, thumbnails); a replay test from a directory of frames producing a known event sequence.
- Live: browser stream on CT123, walk in and out of the webcam's view, watch the event log; then
  the robot's WebRTC stream and the dual-consumer check.

## Order of work

1. After spec 3a: frames, sensor, events, gate, log, Perception page, browser streamer, server-side
   `camera` with detail and region. Testable without the robot and without spec one: events are
   seen and logged, not spoken.
2. With spec one: the session registry and `prompt()` deliver gated events to the open session.
3. With spec two: observations become memories with provenance "observed".
4. Spec four decides when Richard speaks unprompted; the Reachy body plugin adds the robot source.

## Out of scope

The initiative policy itself; memory schema; a small VLM as an intermediate "what changed?" stage
(possible later, on GPU1); audio direction-of-arrival fusion; multiple robots; any code on the
Pi beyond the fallback above; cloud relays.
