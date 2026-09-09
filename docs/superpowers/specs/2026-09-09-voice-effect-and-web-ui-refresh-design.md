# Voice effect and web UI refresh — design

Date: 2026-09-09. Approved by Matteo in dialogue the same day (sections 1–9 below, with two
amendments: a voice-sample upload on the Voice page, and no deeper menu nesting).

## Goal

Two things Matteo asked for after the first live test of Richard on CT123:

1. The Claptrap voice (`clap1`, x-vector mode) is noisy and not robotic enough. Richard gets
   a robot effect applied to every synthesized sentence, tunable from the web UI, and the
   reference clip gets cleaned so the speaker embedding stops carrying the promo's music.
2. The web UI still describes the pre-plugin, Chatterbox-era Richard. Stale controls go,
   missing ones (realtime API, Qwen3-TTS fields, plugins) appear, and the menu stays flat.

Hot reload of plugins is explicitly deferred (Matteo, 2026-09-09: "let's wait"). Brain knobs
(thinking level, per-role brains) belong to spec one. Pitch shifting is not in this spec.

## 1. Voice effect in Richard

New module `src/richard/voice/effects.py`.

`RobotEffect(samplerate: int, strength: int, tone_hz: float)` processes int16 mono PCM and
keeps state between calls so chunked and whole-sentence synthesis sound identical:

- **Band-pass "speaker" colouring**: FIR windowed-sinc band-pass, 250 Hz to 5 kHz, 101 taps
  (about 4 ms of group delay at 24 kHz). The last 100 input samples are kept as state and
  prepended to the next block, so block boundaries are seamless. FIR, not IIR, so numpy's
  `convolve` does the work vectorised; no scipy.
- **Ring modulation**: multiply by a sine at `tone_hz`; the phase accumulator persists across
  calls. Mix = 0.6 × s where s = strength / 100.
- **Bit-depth reduction**: quantise to `16 − 8 × s` bits (16 at s = 0, 8 at s = 1).
- **Gain match**: the output is scaled so its peak equals the input's peak. No clipping, no
  loudness jump against the plain voice.
- **Identity**: strength 0 returns the input bytes unchanged (early return, bit-exact).
- The effect reads the sample rate it is given; a different rate on the next call rebuilds
  the FIR kernel and resets state.

`EffectTTS(inner, effect)` wraps any engine object that has `synth(text) -> bytes` and
`samplerate`; it calls `inner.synth`, then `effect.process(pcm, inner.samplerate)`. Its own
`samplerate` property forwards to the inner engine, so the wrap is invisible to the four
consumers (realtime session, web voice turn, satellite manager, local voice loop).

`cli._build_tts` wraps the built engine when `config.voice.tts_effect != "none"`. Every
engine gets it, not only remote.

Config, under `[voice]`:

| key | type | default | range |
|---|---|---|---|
| `tts_effect` | str | `"none"` | `none`, `robot` (unknown → `none` with one startup line) |
| `tts_effect_strength` | int | 50 | clamped 0–100 on load |
| `tts_effect_tone` | float | 40.0 | clamped 20–200 Hz on load |

Cost: a few milliseconds per sentence (one 101-tap convolution over ~70 k samples). The
first-sound numbers in `Reachy/results` do not move measurably.

## 2. Chatterbox leftovers removed

`tts_exaggeration`, `tts_cfg_weight`, `tts_temperature`, `tts_speed` are removed from the
`Voice` dataclass, `load_config`/`save_config`, `RemoteTTS` (constructor parameters and the
`speed_factor` payload field), `cli._build_tts`, any `--set-*` CLI flags that set them, the web
config API and the page. Old TOML files that still carry the keys load fine: unknown keys in
`[voice]` are ignored, as today. Qwen3-TTS never read these fields; the server has been
ignoring them on every request.

## 3. Voice page

Same drawer page, regrouped into collapsible sections in this order:

1. **Speech-to-text**: engine, model, endpoint, language.
2. **Text-to-speech**: engine, voice (a select fed by the server's voice list when the engine
   is remote and the endpoint answers; falls back to the free-text field), endpoint,
   streaming toggle. A **remote-only block** (model, language, instructions, x-vector only,
   task type) that is hidden unless the engine is `remote`.
3. **Voice sample upload** (remote only): file, voice name, transcript (optional; needed for
   in-context mode, ignored by x-vector mode), a consent checkbox ("I own this recording or
   have permission to use it"), an Upload button, and the current server voice list with the
   uploaded voice selected in the TTS voice select after success. Saving still goes through
   "Save changes". Guidance line: mono, 8–15 s, speech only; the file is forwarded unchanged
   and the server resamples.
4. **Voice effect**: effect (none/robot), strength (0–100 slider), tone (Hz).
5. **Turn-taking**: realtime endpoint silence (ms), VAD aggressiveness and silence (ms)
   labelled "satellites and the local voice loop".
6. **Local microphone**: sample rate, input device, output device, labelled "`richard voice`
   on this machine only; not used by serve".

Backend for the upload and the voice list:

- `GET /api/voices` → proxies `GET {tts_endpoint}/v1/audio/voices`; returns
  `{"voices": [names], "uploaded": [{name, ref_text, created_at}]}`. 503 with a message when
  the engine is not remote or the endpoint does not answer.
- `POST /api/voices` with JSON `{"name", "transcript", "filename", "audio_base64"}` →
  forwards multipart (`audio_sample`, `name`, `ref_text`, `consent`) to
  `POST {tts_endpoint}/v1/audio/voices`. Consent string: `web-<name>-<YYYY-MM-DD>`. Returns the
  server's reply and the refreshed list. JSON with base64 is chosen over multipart so the
  existing JSON route pattern and tests apply; the web server's body limit must allow 4 MB
  (checked in the plan; a 15 s 24 kHz mono wav is 720 KB, about 1 MB in base64).
- Name validation: `[A-Za-z0-9_-]{1,32}`.

## 4. Realtime API

Fields: enabled, host, port, token. The token is write-only like the Home Assistant token:
the config API returns `token: ""` and `token_configured: bool`; an empty PUT leaves it
unchanged, a PUT with a value replaces it. Lede: "The OpenAI-style realtime WebSocket the
Reachy Conversation App connects to. Restart to apply." The config API gains a `realtime`
block in `_config_to_dict` and `_apply_config_update`.

## 5. Plugins

The "Home Assistant" configuration entry becomes **Plugins**. One page, two sections:

1. **Installed plugins**: one row per record from `GET /api/plugins`: name, version,
   configured on/off (from `[plugins] enabled`), running state (from the serve process's
   registry: built, error with its message, disabled, missing), module. An enable toggle per
   row writes the enabled list through `PUT /api/plugins` with `{"name", "enabled"}`; the
   response carries the refreshed rows. A "Restart to apply" button next to the list.
2. **Home Assistant** settings: the existing form, unchanged fields, still writing the plugin
   table through `Config.set_home_assistant`.

`WebApp` gains an injected `plugin_records: Callable[[], list[PluginRecord]] | None`; serve
passes `registry.records`. Without it (tests, chat mode) the page shows configured state only
and running state as "unknown".

## 6. Menu

Matteo: not too much subdivision. The Configuration index shrinks to five pages, each a single
level of collapsible sections:

- Brain
- Personality
- Voice
- Plugins
- Network: Realtime API, Satellite relay, Web access as three sections on one page.

The drawer copy "06 PAGES" becomes "05 PAGES". Nothing gains a third level.

Wording: the System page lede lists exactly what needs a restart (host/port changes, engine
changes, personality, plugin enable/disable, plugin settings). The Home Assistant lede reads
"Save, test the connection, then restart Richard to apply."

## 7. Reference cleanup (Reachy folder, not Richard)

Script `Reachy/voice-refs/claptrap/clean.sh` and `Reachy/scripts/render_voice_samples.py`:

1. demucs (`htdemucs`) on `source.wav` in a dedicated venv `Reachy/.venv-demucs` (CPU on this
   Mac; a 146 s clip takes one to two minutes). Output: the vocals stem.
2. Cut the same window as `clap1` (8.6–20.9 s) from the vocals stem, mono, 24 kHz,
   ffmpeg `loudnorm`. Result `clap1v.wav`, transcript reused from `clap1.txt`.
3. Upload to the TTS server on CT123 as voice `clap1v` (x-vector mode, task type Base) with
   the same consent string convention as the earlier uploads.
4. Render the two Italian and English sample lines used on 2026-09-08 for `clap1` and
   `clap1v`, plain and with the robot effect at strengths 30, 50 and 70 (tone 40 Hz), using the
   effect module from the Richard worktree. Files:
   `Reachy/samples/claptrap-v2/{clap1,clap1v}_{plain,robot30,robot50,robot70}_{it,en}.wav`.
5. Matteo picks by ear. Only then does CT123's config change (`tts_voice`, `tts_effect*`).

## 8. Tests

- Effect: output length equals input length; silence stays silence; strength 0 is bit-exact
  identity; processing one buffer equals processing it as two halves within ±1 LSB; peak of
  the output equals the peak of the input; a sample-rate change rebuilds without error.
- `EffectTTS`: forwards `samplerate`, calls the inner engine once, applies the effect.
- Config: new keys round-trip and clamp; old Chatterbox keys are absent from the dataclass and
  ignored in files.
- `cli._build_tts`: wraps when an effect is set, returns the bare engine otherwise.
- Web API: `/api/voices` GET and POST against a fake TTS server; `/api/plugins` GET and PUT;
  `realtime` block in GET/PUT config with write-only token; page contains the new sections and
  not the removed fields.
- Suite green after every task; one commit per task; push to `public` after the private-string
  check.

## 9. Deploy

Together with spec zero: pull on CT123, restart `richard.service` on Matteo's go (it drops the
live session), then `richard plugins list` and one spoken turn through the web UI as the check.

## Out of scope

Hot reload of plugins (deferred). Brain knobs (spec one). Pitch shifting (v2 if the samples
say the robot still needs it; it costs real latency). Transcoding uploaded samples in Richard
(the server resamples).
