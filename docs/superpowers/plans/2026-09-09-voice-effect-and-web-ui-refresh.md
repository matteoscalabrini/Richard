# Voice Effect and Web UI Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A tunable robot effect on every sentence Richard speaks, a voice-sample upload and Qwen3-TTS fields in the web UI, Chatterbox leftovers gone, and the drawer reorganised around plugins with a flat five-page configuration index.

**Architecture:** `RobotEffect` is a stateful numpy stage (FIR band-pass, ring modulation, bit crush, fixed makeup gain) wrapped around whichever TTS engine `cli._build_tts` builds, so all four output paths get it. A `VoiceLibrary` client talks to the TTS server's `/v1/audio/voices`; the web app proxies it as `/api/voices` and exposes the serve process's plugin registry as `/api/plugins`. The single-page UI in `static.py` gets a regrouped Voice page, a Plugins page (list + the existing Home Assistant form) and a Network page (realtime, relay, web access).

**Tech Stack:** Python 3.11+, numpy (already a base dependency), httpx (base dependency), the existing hand-rolled HTTP server and SPA, pytest. demucs + ffmpeg only in the Reachy-folder workflow, never in Richard.

**Spec:** `docs/superpowers/specs/2026-09-09-voice-effect-and-web-ui-refresh-design.md` (approved 2026-09-09).

## Global Constraints

- Effect config keys under `[voice]`: `tts_effect` (`"none"` | `"robot"`, default `"none"`, unknown → `"none"` with one startup line), `tts_effect_strength` (int, default 50, clamped 0–100 on load), `tts_effect_tone` (float Hz, default 40.0, clamped 20–200 on load).
- Strength 0 returns the input bytes unchanged. Processing one buffer must equal processing it as two halves (the plan's test asserts a max difference of 1 LSB; the implementation is designed to be bit-identical).
- Makeup gain is fixed from the settings, never computed per block (a per-block peak match would break chunk continuity). Output is hard-clipped to int16.
- The four Chatterbox knobs (`tts_exaggeration`, `tts_cfg_weight`, `tts_temperature`, `tts_speed`) disappear from config, `RemoteTTS`, cli, the web API and the page. Old TOML files still load (unknown keys are ignored).
- Voice upload names match `[A-Za-z0-9_-]{1,32}`; samples above 8 MB are refused with 413; the file is forwarded unchanged, the server resamples. Consent string `web-<name>-<YYYY-MM-DD>`.
- The realtime token is shown in the UI like any other field: the config API already echoes it because the browser voice mode needs it to open the socket. (Amends spec section 4, which said write-only.)
- Configuration index: exactly five pages (Brain, Personality, Voice, Plugins, Network); no third level. Drawer copy "05 PAGES".
- Hot reload stays out. Brain knobs stay out. Pitch shifting stays out.
- Every change is TDD: failing test first, minimal code, suite green, commit. Suite: `.venv/bin/python -m pytest -q` from `/Users/matteo/Documents/GitHub/Richard/.worktrees/reachy-presence`, branch `reachy-presence`. Baseline: 560 passed, 2 skipped, 3 deselected at 16218e5.
- Commit messages: imperative subject, a body that says why, trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Before any push to `public`: run the private-string check from the memory `richard-public-push-check`; it must print nothing.
- The Reachy-folder workflow (Task 10) sends synthesis requests to the Qwen3-TTS server on CT123 (GPU1, Richard's card) and uploads one voice there. That is within the standing Richard authorisation; announce it in the session and do not touch CT111.

---

## File structure

**Create**
- `src/richard/voice/effects.py` — `RobotEffect`, `EffectTTS`, `effect_from_config`, `EFFECTS`.
- `src/richard/voice/voices.py` — `VoiceLibrary` (list, upload) against `/v1/audio/voices`.
- `tests/test_voice_effects.py`, `tests/test_voice_library.py`.
- `/Users/matteo/Documents/GitHub/Reachy/voice-refs/claptrap/clean.sh` — demucs + cut + loudnorm → `clap1v.wav`.
- `/Users/matteo/Documents/GitHub/Reachy/scripts/render_voice_samples.py` — renders plain and robot variants for Matteo to pick.

**Modify**
- `src/richard/config.py` — `Voice`: add the three effect keys, remove the four Chatterbox keys; load/save accordingly.
- `src/richard/voice/remote.py` — `RemoteTTS` loses the tuning parameters and the payload update.
- `src/richard/cli.py` — `_build_tts` splits into engine build + effect wrap; remote call loses four kwargs.
- `src/richard/web/app.py` — config API: Qwen fields + effect keys + realtime token; `/api/voices`; `/api/plugins`; two injected factories.
- `src/richard/web/static.py` — Voice page (six sections), Plugins page, Network page, nav, FIELDS, JS.
- `tests/test_config.py`, `tests/test_remote_voice.py`, `tests/test_cli.py`, `tests/test_web.py`.
- `README.md`, `docs/plugins.md`, the spec (two amendments noted in Global Constraints).

Task order keeps the suite green after every task: effect → wrapper and config → remove leftovers → config API → voice library → `/api/voices` → `/api/plugins` → Voice page → Plugins and Network pages → Reachy workflow → docs and deploy.

---

### Task 1: RobotEffect

**Files:**
- Create: `src/richard/voice/effects.py`
- Test: `tests/test_voice_effects.py`

**Interfaces:**
- Produces: `EFFECTS = ("none", "robot")`; `RobotEffect(strength: int = 50, tone_hz: float = 40.0)` with `process(pcm: bytes, samplerate: int) -> bytes` and `reset() -> None`; `effect_from_config(name: str, strength: int, tone_hz: float) -> RobotEffect | None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_voice_effects.py
import numpy as np
import pytest

from richard.voice.effects import EFFECTS, RobotEffect, effect_from_config


def _tone_mix(samplerate=24000, seconds=0.5, amplitude=8000.0):
    t = np.arange(int(samplerate * seconds)) / samplerate
    x = amplitude * (np.sin(2 * np.pi * 400 * t) + 0.5 * np.sin(2 * np.pi * 1000 * t) + 0.25 * np.sin(2 * np.pi * 3000 * t))
    return np.round(x).astype("<i2").tobytes()


def _rms(pcm: bytes) -> float:
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
    return float(np.sqrt(np.mean(x * x))) if x.size else 0.0


def test_effects_list():
    assert EFFECTS == ("none", "robot")


def test_strength_zero_is_bit_exact_identity():
    pcm = _tone_mix()
    assert RobotEffect(strength=0).process(pcm, 24000) == pcm


def test_length_is_preserved_and_odd_trailing_byte_dropped():
    pcm = _tone_mix()
    out = RobotEffect(strength=50).process(pcm, 24000)
    assert len(out) == len(pcm)
    assert len(RobotEffect(strength=50).process(pcm + b"\x01", 24000)) == len(pcm)


def test_silence_stays_silence():
    silence = bytes(2 * 2400)
    assert RobotEffect(strength=100).process(silence, 24000) == silence


def test_two_halves_equal_the_whole():
    pcm = _tone_mix()
    whole = RobotEffect(strength=70, tone_hz=40.0).process(pcm, 24000)
    split = RobotEffect(strength=70, tone_hz=40.0)
    half = len(pcm) // 2
    half -= half % 2
    joined = split.process(pcm[:half], 24000) + split.process(pcm[half:], 24000)
    a = np.frombuffer(whole, dtype="<i2").astype(np.int32)
    b = np.frombuffer(joined, dtype="<i2").astype(np.int32)
    assert int(np.max(np.abs(a - b))) <= 1


def test_loudness_stays_within_3_db_of_input():
    pcm = _tone_mix()
    for strength in (30, 50, 100):
        out = RobotEffect(strength=strength).process(pcm, 24000)
        ratio = _rms(out) / _rms(pcm)
        assert 0.6 <= ratio <= 1.4, (strength, ratio)


def test_output_never_leaves_int16_range():
    pcm = _tone_mix(amplitude=32000.0)
    out = np.frombuffer(RobotEffect(strength=100).process(pcm, 24000), dtype="<i2")
    assert out.min() >= -32768 and out.max() <= 32767


def test_effect_actually_changes_the_signal():
    pcm = _tone_mix()
    out = RobotEffect(strength=50).process(pcm, 24000)
    assert out != pcm


def test_sample_rate_change_rebuilds_without_error():
    effect = RobotEffect(strength=50)
    pcm24 = _tone_mix(24000)
    pcm16 = _tone_mix(16000)
    assert len(effect.process(pcm24, 24000)) == len(pcm24)
    assert len(effect.process(pcm16, 16000)) == len(pcm16)


def test_strength_and_tone_are_clamped():
    effect = RobotEffect(strength=250, tone_hz=0.0)
    assert effect.strength == 100
    assert effect.tone_hz == 1.0


def test_effect_from_config():
    assert effect_from_config("none", 50, 40.0) is None
    assert effect_from_config("mystery", 50, 40.0) is None
    robot = effect_from_config("robot", 30, 55.0)
    assert isinstance(robot, RobotEffect)
    assert robot.strength == 30 and robot.tone_hz == 55.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_voice_effects.py -q`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'richard.voice.effects'`

- [ ] **Step 3: Write the implementation**

```python
# src/richard/voice/effects.py
"""Post-processing effects on synthesized speech (spec 2026-09-09).

An effect runs on int16 mono PCM after any TTS engine and before any transport, so
every output path (realtime, web, satellites, local loop) sounds the same. State (the
FIR tail and the ring-modulator sample counter) persists across calls: a sentence
processed in chunks equals the same sentence processed whole.
"""
from __future__ import annotations

import numpy as np

EFFECTS = ("none", "robot")


def _bandpass_kernel(samplerate: int, low_hz: float, high_hz: float, taps: int) -> np.ndarray:
    """Windowed-sinc band-pass (Hamming), unity gain at the band's geometric centre."""
    n = np.arange(taps) - (taps - 1) / 2.0

    def lowpass(cutoff_hz: float) -> np.ndarray:
        fc = cutoff_hz / samplerate
        return 2.0 * fc * np.sinc(2.0 * fc * n)

    kernel = (lowpass(high_hz) - lowpass(low_hz)) * np.hamming(taps)
    centre = 2.0 * np.pi * np.sqrt(low_hz * high_hz) / samplerate
    gain = abs(np.sum(kernel * np.exp(-1j * centre * np.arange(taps))))
    return (kernel / gain).astype(np.float32)


class RobotEffect:
    """Speaker colouring + ring modulation + bit crush, with a fixed makeup gain."""

    def __init__(
        self,
        strength: int = 50,
        tone_hz: float = 40.0,
        *,
        taps: int = 101,
        low_hz: float = 250.0,
        high_hz: float = 5000.0,
    ) -> None:
        self.strength = max(0, min(100, int(strength)))
        self.tone_hz = max(1.0, float(tone_hz))
        self._taps = taps
        self._low = low_hz
        self._high = high_hz
        self._samplerate: int | None = None
        self._kernel: np.ndarray | None = None
        self._tail = np.zeros(taps - 1, dtype=np.float32)
        self._count = 0  # samples seen since reset; drives the modulator phase exactly

    def reset(self) -> None:
        self._tail = np.zeros(self._taps - 1, dtype=np.float32)
        self._count = 0

    def _prepare(self, samplerate: int) -> None:
        if samplerate != self._samplerate:
            self._samplerate = samplerate
            self._kernel = _bandpass_kernel(samplerate, self._low, self._high, self._taps)
            self.reset()

    def process(self, pcm: bytes, samplerate: int) -> bytes:
        if self.strength == 0:
            return pcm
        usable = len(pcm) - len(pcm) % 2
        if usable == 0:
            return b""
        self._prepare(samplerate)
        s = self.strength / 100.0
        x = np.frombuffer(pcm[:usable], dtype="<i2").astype(np.float32)

        # 1. Speaker colouring: FIR band-pass with the previous block's tail prepended,
        #    so the filter never sees a block boundary.
        padded = np.concatenate([self._tail, x])
        filtered = np.convolve(padded, self._kernel, mode="valid").astype(np.float32)
        self._tail = padded[-(self._taps - 1):].copy()
        y = x + s * (filtered - x)

        # 2. Ring modulation. The carrier argument is omega * absolute sample index, so a
        #    chunk boundary produces the same float64 arguments as one long buffer.
        omega = 2.0 * np.pi * self.tone_hz / samplerate
        index = self._count + np.arange(x.size, dtype=np.int64)
        carrier = np.sin(omega * index.astype(np.float64)).astype(np.float32)
        self._count += int(x.size)
        mix = 0.6 * s
        y = y * (1.0 - mix) + y * carrier * mix

        # 3. Bit-depth reduction: 16 bits at s=0 down to 8 bits at s=1.
        step = 2.0 ** (8.0 * s)
        y = np.round(y / step) * step

        # 4. Fixed makeup gain for the ring-modulation loss (a sine's RMS is 1/sqrt 2).
        #    Computed from the settings, never from the block, so chunks stay seamless.
        y = y / (1.0 - mix + mix / np.sqrt(2.0))
        return np.clip(np.round(y), -32768, 32767).astype("<i2").tobytes()


def effect_from_config(name: str, strength: int, tone_hz: float) -> RobotEffect | None:
    """The effect for the [voice] settings; None for "none" and for unknown names."""
    if name == "robot":
        return RobotEffect(strength=strength, tone_hz=tone_hz)
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_voice_effects.py -q`
Expected: 11 passed. If `test_two_halves_equal_the_whole` fails with a difference of exactly one crush step, the cause is float summation order inside `np.convolve` on this platform; fix by computing the FIR as `np.correlate` over the same padded array (identical per-sample windows), not by loosening the test.

- [ ] **Step 5: Commit**

```bash
git add src/richard/voice/effects.py tests/test_voice_effects.py
git commit -m "voice: RobotEffect, a stateful post-processing stage for synthesized speech

Band-pass colouring, ring modulation and bit crush driven by one strength knob and
one tone knob; FIR tail and modulator index persist so chunked and whole synthesis
are identical. Fixed makeup gain, never per block.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: EffectTTS wrapper, config keys, TTS builder

**Files:**
- Modify: `src/richard/voice/effects.py` (append `EffectTTS`)
- Modify: `src/richard/config.py` (`Voice` dataclass at the `tts_*` fields; `load_config` voice block; `save_config` `voice_table`)
- Modify: `src/richard/cli.py` (`_build_tts`)
- Test: `tests/test_voice_effects.py`, `tests/test_config.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `RobotEffect`, `effect_from_config`, `EFFECTS` from Task 1.
- Produces: `EffectTTS(inner, effect)` with `synth(text) -> bytes` and `samplerate`; `Voice.tts_effect: str`, `Voice.tts_effect_strength: int`, `Voice.tts_effect_tone: float`; `cli._build_tts_engine(config, write)` (the old body) and `cli._build_tts(config, write)` returning the wrapped engine.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_voice_effects.py`:

```python
from richard.voice.effects import EffectTTS


class _Engine:
    samplerate = 24000

    def __init__(self):
        self.calls = []

    def synth(self, text):
        self.calls.append(text)
        return _tone_mix()


def test_effect_tts_forwards_samplerate_and_applies_effect():
    engine = _Engine()
    wrapped = EffectTTS(engine, RobotEffect(strength=50))
    out = wrapped.synth("hello")
    assert wrapped.samplerate == 24000
    assert engine.calls == ["hello"]
    assert len(out) == len(_tone_mix()) and out != _tone_mix()
```

Append to `tests/test_config.py`:

```python
def test_voice_effect_defaults_and_roundtrip(tmp_path):
    cfg = Config()
    assert (cfg.voice.tts_effect, cfg.voice.tts_effect_strength, cfg.voice.tts_effect_tone) == ("none", 50, 40.0)
    cfg.voice.tts_effect = "robot"
    cfg.voice.tts_effect_strength = 70
    cfg.voice.tts_effect_tone = 55.0
    path = tmp_path / "config.toml"
    save_config(cfg, path)
    v = load_config(path).voice
    assert (v.tts_effect, v.tts_effect_strength, v.tts_effect_tone) == ("robot", 70, 55.0)


def test_voice_effect_clamped_on_load(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[voice]\ntts_effect = "robot"\ntts_effect_strength = 250\ntts_effect_tone = 5\n')
    v = load_config(path).voice
    assert v.tts_effect_strength == 100
    assert v.tts_effect_tone == 20.0
```

Append to `tests/test_cli.py`:

```python
def _fake_remote_engine(monkeypatch):
    import richard.voice.remote as remote_mod

    class FakeRemoteTTS:
        samplerate = 24000

        def __init__(self, endpoint, voice, **kwargs):
            self.kwargs = kwargs

        def synth(self, text):
            return b"\x10\x00" * 2400

    monkeypatch.setattr(remote_mod, "RemoteTTS", FakeRemoteTTS)
    return FakeRemoteTTS


def _remote_config():
    from richard.config import Config

    config = Config()
    config.voice.tts_engine = "remote"
    config.voice.tts_endpoint = "http://127.0.0.1:8091"
    config.voice.tts_voice = "clap1"
    return config


def test_build_tts_returns_the_bare_engine_without_an_effect(monkeypatch):
    fake = _fake_remote_engine(monkeypatch)
    engine = cli._build_tts(_remote_config(), lambda s: None)
    assert isinstance(engine, fake)


def test_build_tts_wraps_the_engine_when_an_effect_is_set(monkeypatch):
    from richard.voice.effects import EffectTTS

    _fake_remote_engine(monkeypatch)
    config = _remote_config()
    config.voice.tts_effect = "robot"
    engine = cli._build_tts(config, lambda s: None)
    assert isinstance(engine, EffectTTS)
    assert engine.samplerate == 24000
    assert len(engine.synth("ciao")) == 4800


def test_build_tts_unknown_effect_speaks_plain_and_says_so(monkeypatch):
    fake = _fake_remote_engine(monkeypatch)
    config = _remote_config()
    config.voice.tts_effect = "vocoder"
    lines = []
    engine = cli._build_tts(config, lines.append)
    assert isinstance(engine, fake)
    assert lines == ["Unknown voice effect 'vocoder'; speaking without an effect."]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_voice_effects.py tests/test_config.py tests/test_cli.py -q -k "effect"`
Expected: FAIL with `ImportError: cannot import name 'EffectTTS'` and `AttributeError: 'Voice' object has no attribute 'tts_effect'`

- [ ] **Step 3: Implement**

Append to `src/richard/voice/effects.py`:

```python
class EffectTTS:
    """Wraps any engine exposing `synth(text) -> bytes` and `samplerate`; applies the effect."""

    def __init__(self, inner, effect: RobotEffect) -> None:
        self._inner = inner
        self._effect = effect

    @property
    def samplerate(self) -> int:
        return self._inner.samplerate

    def synth(self, text: str) -> bytes:
        return self._effect.process(self._inner.synth(text), self._inner.samplerate)
```

In `src/richard/config.py`, in the `Voice` dataclass after `tts_speed` add:

```python
    tts_effect: str = "none"  # post-processing on every spoken sentence: none | robot
    tts_effect_strength: int = 50  # 0-100, drives ring-mod mix, bit crush and band-pass together
    tts_effect_tone: float = 40.0  # ring-modulator frequency in Hz, 20-200
```

In `load_config`, in the `Voice(...)` call after `tts_speed=...` add:

```python
        tts_effect=str(v.get("tts_effect", Voice.tts_effect)) or Voice.tts_effect,
        tts_effect_strength=max(0, min(100, int(v.get("tts_effect_strength", Voice.tts_effect_strength)))),
        tts_effect_tone=max(20.0, min(200.0, float(v.get("tts_effect_tone", Voice.tts_effect_tone)))),
```

In `save_config`, in `voice_table` after `"tts_speed": config.voice.tts_speed,` add:

```python
        "tts_effect": config.voice.tts_effect,
        "tts_effect_strength": config.voice.tts_effect_strength,
        "tts_effect_tone": config.voice.tts_effect_tone,
```

In `src/richard/cli.py` rename the existing `_build_tts` to `_build_tts_engine` (same body, docstring "Build the configured TTS engine without post-processing.") and add:

```python
def _build_tts(config, write: Callable[[str], None]):
    """The configured TTS engine, wrapped in the configured voice effect if any."""
    from richard.voice.effects import EFFECTS, EffectTTS, effect_from_config

    engine = _build_tts_engine(config, write)
    name = config.voice.tts_effect
    if name == "none":
        return engine
    if name not in EFFECTS:
        write(f"Unknown voice effect {name!r}; speaking without an effect.")
        return engine
    effect = effect_from_config(name, config.voice.tts_effect_strength, config.voice.tts_effect_tone)
    return EffectTTS(engine, effect) if effect is not None else engine
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_voice_effects.py tests/test_config.py tests/test_cli.py -q`
Expected: all passed

- [ ] **Step 5: Full suite, then commit**

Run: `.venv/bin/python -m pytest -q` — Expected: all passed.

```bash
git add src/richard/voice/effects.py src/richard/config.py src/richard/cli.py tests/test_voice_effects.py tests/test_config.py tests/test_cli.py
git commit -m "voice: tts_effect config and EffectTTS wrap in the TTS builder

Three [voice] keys (effect, strength, tone); every engine, not only remote, is
wrapped, so realtime, web, satellites and the local loop all speak the same way.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Remove the Chatterbox knobs

**Files:**
- Modify: `src/richard/config.py` (`Voice` fields `tts_exaggeration`, `tts_cfg_weight`, `tts_temperature`, `tts_speed`; their four `load_config` lines; their four `save_config` lines)
- Modify: `src/richard/voice/remote.py` (`RemoteTTS.__init__` parameters `exaggeration`, `cfg_weight`, `temperature`, `speed_factor`; `self._tuning`; the `payload.update(...)` line in `synth`)
- Modify: `src/richard/cli.py` (`_build_tts_engine`, the four kwargs in the `RemoteTTS(...)` call)
- Modify: `src/richard/web/app.py` (`_config_to_dict` voice block: four lines; `_apply_config_update` voice block: the four `if "tts_exaggeration" ...` through `"tts_speed"` blocks)
- Modify: `src/richard/web/static.py` (FIELDS: four `voice.tts_*` entries; markup: the "TTS tuning — remote (Chatterbox) engine only" `setting-group` and the two `field-row` divs holding the four inputs)
- Test: `tests/test_config.py` (`test_voice_tts_tuning_defaults`, `test_voice_tts_tuning_roundtrip`), `tests/test_remote_voice.py` (`test_remote_tts_sends_tuning_params`), `tests/test_web.py` (`test_get_config_exposes_tts_tuning`, `test_put_sets_and_clamps_tts_tuning`)

- [ ] **Step 1: Replace the tests that pin the knobs**

Delete `test_voice_tts_tuning_defaults` and `test_voice_tts_tuning_roundtrip` from `tests/test_config.py` and add:

```python
def test_chatterbox_knobs_are_gone_and_ignored_in_old_files(tmp_path):
    for name in ("tts_exaggeration", "tts_cfg_weight", "tts_temperature", "tts_speed"):
        assert not hasattr(Config().voice, name)
    path = tmp_path / "config.toml"
    path.write_text('[voice]\ntts_exaggeration = 1.2\ntts_speed = 1.3\ntts_voice = "clap1"\n')
    assert load_config(path).voice.tts_voice == "clap1"
```

Replace `test_remote_tts_sends_tuning_params` in `tests/test_remote_voice.py` with:

```python
def test_remote_tts_sends_no_chatterbox_tuning_fields():
    captured = {}

    def handler(request):
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, content=_wav(b"\x00\x00" * 10, 24000))

    RemoteTTS("http://host:8004", voice="tars", client=_client(handler)).synth("hi")
    for key in ("exaggeration", "cfg_weight", "temperature", "speed_factor"):
        assert key not in captured["json"]
```

Replace `test_get_config_exposes_tts_tuning` and `test_put_sets_and_clamps_tts_tuning` in `tests/test_web.py` with:

```python
def test_config_api_has_no_chatterbox_knobs(tmp_path):
    app = _app(tmp_path)
    v = _body(app.handle("GET", "/api/config"))["voice"]
    for key in ("tts_exaggeration", "tts_cfg_weight", "tts_temperature", "tts_speed"):
        assert key not in v
    resp = app.handle("PUT", "/api/config", json.dumps({"voice": {"tts_speed": 1.4}}).encode())
    assert _body(resp)["changed"] == []
    html = app.handle("GET", "/").body.decode()
    assert 'id="voice.tts_exaggeration"' not in html
    assert "Chatterbox" not in html
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_config.py tests/test_remote_voice.py tests/test_web.py -q -k "chatterbox"`
Expected: 3 failed (`hasattr` is True; `exaggeration` is in the payload; the page still has the field)

- [ ] **Step 3: Remove the knobs**

`src/richard/config.py`: delete the four `Voice` fields and their four `load_config` lines (`tts_exaggeration=float(...)` … `tts_speed=float(...)`) and their four `save_config` lines.

`src/richard/voice/remote.py`: delete the four constructor parameters, the `# Chatterbox generation knobs` comment and the `self._tuning = {...}` block, and the line `payload.update({k: v for k, v in self._tuning.items() if v is not None})` in `synth`. Update the class docstring's first line to `"""TTS via an OpenAI-compatible /v1/audio/speech endpoint (vLLM-Omni/Qwen3-TTS)."""`.

`src/richard/cli.py`: in `_build_tts_engine` delete the four kwargs `exaggeration=…`, `cfg_weight=…`, `temperature=…`, `speed_factor=…`.

`src/richard/web/app.py`: delete the four lines in `_config_to_dict["voice"]` and the four `if "tts_…" in v:` blocks in `_apply_config_update`.

`src/richard/web/static.py`: delete the four FIELDS entries (`voice.tts_exaggeration`, `voice.tts_cfg_weight`, `voice.tts_temperature`, `voice.tts_speed`) and, in the Voice page markup, the `setting-group` whose label reads "TTS tuning — remote (Chatterbox) engine only" together with the two `field-row` divs that follow it (Exaggeration/CFG weight and Temperature/Speed).

Then: `grep -rn "exaggeration\|cfg_weight\|speed_factor\|tts_temperature\|tts_speed\|Chatterbox" src tests` must print nothing.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_config.py tests/test_remote_voice.py tests/test_web.py tests/test_cli.py -q`
Expected: all passed

- [ ] **Step 5: Full suite, then commit**

```bash
git add src/richard/config.py src/richard/voice/remote.py src/richard/cli.py src/richard/web/app.py src/richard/web/static.py tests/test_config.py tests/test_remote_voice.py tests/test_web.py
git commit -m "voice: drop the Chatterbox tuning knobs

Qwen3-TTS never read exaggeration, cfg_weight, temperature or speed_factor; they
were sent on every request and shown in the UI as if they did something. Old
config files still load.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Config API carries the Qwen3-TTS fields, the effect keys and the realtime token

**Files:**
- Modify: `src/richard/web/app.py` (`_config_to_dict["voice"]`; `_apply_config_update` voice block after `output_device`; realtime block after `port`)
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `EFFECTS` from Task 1; `Voice.tts_effect*` from Task 2.
- Produces: `GET /api/config` voice block includes `tts_model`, `tts_language`, `tts_instructions`, `tts_xvec_only`, `tts_task_type`, `tts_effect`, `tts_effect_strength`, `tts_effect_tone`; `PUT /api/config` accepts them (effect name validated against `EFFECTS`, strength clamped 0–100, tone clamped 20–200) and `realtime.token`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_web.py`:

```python
def test_config_api_round_trips_qwen_fields_and_effect(tmp_path):
    app = _app(tmp_path)
    v = _body(app.handle("GET", "/api/config"))["voice"]
    assert v["tts_model"] == "chatterbox" and v["tts_xvec_only"] is False and v["tts_effect"] == "none"
    patch = {"voice": {
        "tts_model": "", "tts_language": "Italian", "tts_instructions": "dry", "tts_xvec_only": True,
        "tts_task_type": "Base", "tts_effect": "robot", "tts_effect_strength": 70, "tts_effect_tone": 55,
    }}
    data = _body(app.handle("PUT", "/api/config", json.dumps(patch).encode()))
    assert "voice.tts_effect" in data["changed"] and "voice.tts_xvec_only" in data["changed"]
    v = load_config(app._config_path).voice
    assert (v.tts_model, v.tts_language, v.tts_instructions, v.tts_xvec_only, v.tts_task_type) == ("", "Italian", "dry", True, "Base")
    assert (v.tts_effect, v.tts_effect_strength, v.tts_effect_tone) == ("robot", 70, 55.0)


def test_put_clamps_effect_knobs_and_rejects_unknown_effect(tmp_path):
    app = _app(tmp_path)
    app.handle("PUT", "/api/config", json.dumps({"voice": {"tts_effect": "vocoder", "tts_effect_strength": 500, "tts_effect_tone": 1}}).encode())
    v = load_config(app._config_path).voice
    assert (v.tts_effect, v.tts_effect_strength, v.tts_effect_tone) == ("none", 100, 20.0)


def test_put_realtime_token(tmp_path):
    app = _app(tmp_path)
    data = _body(app.handle("PUT", "/api/config", json.dumps({"realtime": {"token": "s3cret", "port": 8767}}).encode()))
    assert "realtime.token" in data["changed"]
    rt = load_config(app._config_path).realtime
    assert (rt.token, rt.port) == ("s3cret", 8767)
    assert _body(app.handle("GET", "/api/config"))["realtime"]["token"] == "s3cret"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_web.py -q -k "qwen_fields or clamps_effect or realtime_token"`
Expected: 3 failed (`KeyError: 'tts_model'`, effect not applied, `realtime.token` not in changed)

- [ ] **Step 3: Implement**

In `_config_to_dict["voice"]` after `"tts_streaming": config.voice.tts_streaming,` add:

```python
            "tts_model": config.voice.tts_model,
            "tts_language": config.voice.tts_language,
            "tts_instructions": config.voice.tts_instructions,
            "tts_xvec_only": config.voice.tts_xvec_only,
            "tts_task_type": config.voice.tts_task_type,
            "tts_effect": config.voice.tts_effect,
            "tts_effect_strength": config.voice.tts_effect_strength,
            "tts_effect_tone": config.voice.tts_effect_tone,
```

In `_apply_config_update`, after the `output_device` block inside `if isinstance(v, dict):` add:

```python
        for key in ("tts_model", "tts_language", "tts_instructions", "tts_task_type"):
            if key in v:
                setattr(config.voice, key, str(v[key] or ""))
                changed.append(f"voice.{key}")
        if "tts_xvec_only" in v:
            config.voice.tts_xvec_only = _as_bool(v["tts_xvec_only"])
            changed.append("voice.tts_xvec_only")
        if "tts_effect" in v:
            name = str(v["tts_effect"] or "none")
            config.voice.tts_effect = name if name in EFFECTS else "none"
            changed.append("voice.tts_effect")
        if "tts_effect_strength" in v:
            config.voice.tts_effect_strength = max(0, min(100, _as_int(v["tts_effect_strength"], 50)))
            changed.append("voice.tts_effect_strength")
        if "tts_effect_tone" in v:
            config.voice.tts_effect_tone = _clamp_float(v["tts_effect_tone"], 20.0, 200.0)
            changed.append("voice.tts_effect_tone")
```

with `from richard.voice.effects import EFFECTS` added to the module imports. In the realtime block after the `port` handling add:

```python
        if "token" in rt:
            config.realtime.token = str(rt["token"] or "")
            changed.append("realtime.token")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_web.py -q`
Expected: all passed

- [ ] **Step 5: Full suite, then commit**

```bash
git add src/richard/web/app.py tests/test_web.py
git commit -m "web: config API carries Qwen3-TTS fields, the voice effect and the realtime token

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: VoiceLibrary client

**Files:**
- Create: `src/richard/voice/voices.py`
- Test: `tests/test_voice_library.py`

**Interfaces:**
- Produces: `VoiceLibrary(endpoint: str, *, client: httpx.Client | None = None, timeout: float = 120.0)` with `list() -> dict` (`{"voices": [str], "uploaded": [{"name", "ref_text", "created_at"}]}`) and `upload(name: str, audio: bytes, filename: str, *, transcript: str = "", consent: str) -> dict`. Both raise `httpx.HTTPError` on transport or status errors.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_voice_library.py
import json

import httpx

from richard.voice.voices import VoiceLibrary


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_list_parses_names_and_uploaded_metadata():
    def handler(request):
        assert request.method == "GET" and str(request.url).endswith("/v1/audio/voices")
        return httpx.Response(200, json={
            "voices": ["clap1", "default"],
            "uploaded_voices": [{"name": "clap1", "ref_text": "oh hi", "created_at": 1788950000, "file_size": 1}],
        })

    library = VoiceLibrary("http://host:8091/", client=_client(handler))
    assert library.list() == {"voices": ["clap1", "default"], "uploaded": [{"name": "clap1", "ref_text": "oh hi", "created_at": 1788950000}]}


def test_upload_sends_multipart_fields():
    captured = {}

    def handler(request):
        captured["content_type"] = request.headers["content-type"]
        captured["body"] = request.content
        return httpx.Response(200, json={"name": "clap1v"})

    library = VoiceLibrary("http://host:8091", client=_client(handler))
    reply = library.upload("clap1v", b"RIFF....", "clap1v.wav", transcript="oh hi", consent="web-clap1v-2026-09-09")
    assert reply == {"name": "clap1v"}
    assert captured["content_type"].startswith("multipart/form-data")
    body = captured["body"]
    for needle in (b'name="audio_sample"; filename="clap1v.wav"', b"audio/wav", b'name="name"', b"clap1v", b'name="ref_text"', b"oh hi", b'name="consent"', b"web-clap1v-2026-09-09", b"RIFF...."):
        assert needle in body


def test_errors_propagate_as_httpx_errors():
    library = VoiceLibrary("http://host:8091", client=_client(lambda request: httpx.Response(500, text="boom")))
    try:
        library.list()
    except httpx.HTTPError:
        pass
    else:
        raise AssertionError("expected an httpx error")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_voice_library.py -q`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'richard.voice.voices'`

- [ ] **Step 3: Implement**

```python
# src/richard/voice/voices.py
"""The TTS server's voice library: vLLM-Omni / Qwen3-TTS `/v1/audio/voices`.

Upload once, the server keeps the sample and its speaker embedding across restarts.
The file is forwarded unchanged; the server resamples.
"""
from __future__ import annotations

import mimetypes

import httpx


class VoiceLibrary:
    def __init__(self, endpoint: str, *, client: httpx.Client | None = None, timeout: float = 120.0) -> None:
        self._url = endpoint.rstrip("/") + "/v1/audio/voices"
        self._client = client or httpx.Client(timeout=timeout)

    def list(self) -> dict:
        response = self._client.get(self._url)
        response.raise_for_status()
        data = response.json()
        uploaded = [
            {
                "name": str(item.get("name", "")),
                "ref_text": str(item.get("ref_text") or ""),
                "created_at": item.get("created_at"),
            }
            for item in data.get("uploaded_voices", [])
            if isinstance(item, dict)
        ]
        return {"voices": [str(name) for name in data.get("voices", [])], "uploaded": uploaded}

    def upload(self, name: str, audio: bytes, filename: str, *, transcript: str = "", consent: str) -> dict:
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        response = self._client.post(
            self._url,
            files={"audio_sample": (filename, audio, mime)},
            data={"name": name, "ref_text": transcript, "consent": consent},
        )
        response.raise_for_status()
        try:
            return response.json()
        except ValueError:
            return {}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_voice_library.py -q`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/richard/voice/voices.py tests/test_voice_library.py
git commit -m "voice: VoiceLibrary client for the TTS server's /v1/audio/voices

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: `/api/voices` list and upload

**Files:**
- Modify: `src/richard/web/app.py` (imports; `WebApp.__init__` parameters; `_dispatch`; new handlers)
- Test: `tests/test_web.py` (`_app` helper gains a `voice_library_factory` pass-through)

**Interfaces:**
- Consumes: `VoiceLibrary` from Task 5.
- Produces: `WebApp(..., voice_library_factory: Callable[[str], object] = VoiceLibrary)`; `GET /api/voices` → 200 `{"voices", "uploaded"}` or 503 `{"error", "voices": [], "uploaded": []}`; `POST /api/voices` JSON `{"name", "transcript", "filename", "audio_base64"}` → 200 `{"uploaded": name, "voices", "uploaded_list"}`, 400 on bad input, 413 above 8 MB, 503 without a remote engine, 502 when the server refuses.

- [ ] **Step 1: Write the failing tests**

In `tests/test_web.py` extend `_app`: add parameter `voice_library_factory=None` and, next to the existing `home_assistant_client_factory` pass-through, `if voice_library_factory is not None: kwargs["voice_library_factory"] = voice_library_factory`. Then append:

```python
import base64


class FakeVoiceLibrary:
    def __init__(self, endpoint, fail=False):
        self.endpoint = endpoint
        self.fail = fail
        self.uploads = []
        self.names = ["clap1", "default"]

    def list(self):
        if self.fail:
            raise RuntimeError("server down")
        return {"voices": list(self.names), "uploaded": [{"name": "clap1", "ref_text": "oh hi", "created_at": 1}]}

    def upload(self, name, audio, filename, *, transcript="", consent=""):
        self.uploads.append((name, audio, filename, transcript, consent))
        self.names.append(name)
        return {"name": name}


def _remote_app(tmp_path, library):
    app = _app(tmp_path, voice_library_factory=lambda endpoint: library)
    config = load_config(app._config_path)
    config.voice.tts_engine = "remote"
    config.voice.tts_endpoint = "http://tts:8091"
    save_config(config, app._config_path)
    return app


def test_voices_list_needs_the_remote_engine(tmp_path):
    resp = _app(tmp_path, voice_library_factory=lambda endpoint: FakeVoiceLibrary(endpoint)).handle("GET", "/api/voices")
    assert resp.status == 503
    assert "remote" in _body(resp)["error"]


def test_voices_list_proxies_the_server(tmp_path):
    library = FakeVoiceLibrary("unused")
    data = _body(_remote_app(tmp_path, library).handle("GET", "/api/voices"))
    assert data["voices"] == ["clap1", "default"]
    assert data["uploaded"][0]["name"] == "clap1"


def test_voices_list_reports_a_dead_server(tmp_path):
    resp = _remote_app(tmp_path, FakeVoiceLibrary("unused", fail=True)).handle("GET", "/api/voices")
    assert resp.status == 503 and "server down" in _body(resp)["error"]


def test_voice_upload_forwards_the_sample_and_refreshes_the_list(tmp_path):
    library = FakeVoiceLibrary("unused")
    app = _remote_app(tmp_path, library)
    payload = {"name": "clap1v", "transcript": "oh hi", "filename": "clap1v.wav", "audio_base64": base64.b64encode(b"RIFF....").decode()}
    resp = app.handle("POST", "/api/voices", json.dumps(payload).encode())
    assert resp.status == 200, resp.body
    data = _body(resp)
    assert data["uploaded"] == "clap1v" and "clap1v" in data["voices"]
    name, audio, filename, transcript, consent = library.uploads[0]
    assert (name, audio, filename, transcript) == ("clap1v", b"RIFF....", "clap1v.wav", "oh hi")
    assert consent.startswith("web-clap1v-")


def test_voice_upload_validates_name_audio_and_size(tmp_path):
    app = _remote_app(tmp_path, FakeVoiceLibrary("unused"))
    bad_name = {"name": "bad name!", "audio_base64": base64.b64encode(b"x").decode()}
    assert app.handle("POST", "/api/voices", json.dumps(bad_name).encode()).status == 400
    no_audio = {"name": "ok", "audio_base64": ""}
    assert app.handle("POST", "/api/voices", json.dumps(no_audio).encode()).status == 400
    not_b64 = {"name": "ok", "audio_base64": "@@@"}
    assert app.handle("POST", "/api/voices", json.dumps(not_b64).encode()).status == 400
    huge = {"name": "ok", "audio_base64": base64.b64encode(b"\0" * (8 * 1024 * 1024 + 1)).decode()}
    assert app.handle("POST", "/api/voices", json.dumps(huge).encode()).status == 413
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_web.py -q -k "voices or voice_upload"`
Expected: FAIL with `TypeError: WebApp.__init__() got an unexpected keyword argument 'voice_library_factory'` and 404s

- [ ] **Step 3: Implement**

In `src/richard/web/app.py` add imports `import binascii`, `import re`, `from datetime import date`, and `from richard.voice.voices import VoiceLibrary`; module constants:

```python
_VOICE_NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")
_MAX_VOICE_SAMPLE_BYTES = 8 * 1024 * 1024
```

`WebApp.__init__`: add parameter `voice_library_factory: Callable[[str], object] = VoiceLibrary,` after `home_assistant_client_factory` and store `self._voice_library_factory = voice_library_factory`.

`_dispatch`, before the `/api/restart` line:

```python
        if method == "GET" and path == "/api/voices":
            return self._list_voices()
        if method == "POST" and path == "/api/voices":
            return self._upload_voice(body)
```

Handlers (place after `_home_assistant_status`):

```python
    # --- voice library (remote TTS server) ---

    def _voice_library(self):
        config = self._load(self._config_path)
        if config.voice.tts_engine != "remote" or not config.voice.tts_endpoint:
            return None, "The voice library needs the remote TTS engine and its endpoint."
        return self._voice_library_factory(config.voice.tts_endpoint), None

    def _list_voices(self) -> Response:
        library, error = self._voice_library()
        if library is None:
            return Response.json({"error": error, "voices": [], "uploaded": []}, 503)
        try:
            return Response.json(library.list())
        except Exception as exc:  # noqa: BLE001 — an unreachable server is feedback, not a crash
            return Response.json({"error": f"voice list failed: {exc}", "voices": [], "uploaded": []}, 503)

    def _upload_voice(self, body: bytes) -> Response:
        try:
            payload = json.loads(body or b"{}")
        except (ValueError, TypeError) as exc:
            return Response.bad_request(f"invalid JSON: {exc}")
        if not isinstance(payload, dict):
            return Response.bad_request("expected an object")
        name = str(payload.get("name", "")).strip()
        if not _VOICE_NAME_RE.fullmatch(name):
            return Response.bad_request("voice name: letters, digits, _ and -, at most 32 characters")
        try:
            audio = base64.b64decode(str(payload.get("audio_base64", "")), validate=True)
        except (ValueError, binascii.Error):
            return Response.bad_request("audio_base64 is not valid base64")
        if not audio:
            return Response.bad_request("an audio file is required")
        if len(audio) > _MAX_VOICE_SAMPLE_BYTES:
            return Response.json({"error": "sample larger than 8 MB"}, 413)
        library, error = self._voice_library()
        if library is None:
            return Response.json({"error": error}, 503)
        filename = str(payload.get("filename") or f"{name}.wav")
        consent = f"web-{name}-{date.today().isoformat()}"
        try:
            library.upload(name, audio, filename, transcript=str(payload.get("transcript") or ""), consent=consent)
            listing = library.list()
        except Exception as exc:  # noqa: BLE001
            return Response.json({"error": f"upload failed: {exc}"}, 502)
        return Response.json({"uploaded": name, "voices": listing["voices"], "uploaded_list": listing["uploaded"]})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_web.py -q`
Expected: all passed

- [ ] **Step 5: Full suite, then commit**

```bash
git add src/richard/web/app.py tests/test_web.py
git commit -m "web: /api/voices lists the TTS server's voices and uploads a sample

JSON with base64 audio keeps the existing route pattern; the sample goes to the
server unchanged with a consent string, and the refreshed list comes back.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: `/api/plugins` list and enable/disable

**Files:**
- Modify: `src/richard/web/app.py` (`WebApp.__init__`; `_dispatch`; new handlers)
- Modify: `src/richard/cli.py` (`_run_serve`, the `WebApp(...)` call: pass `plugin_records=registry.records`)
- Test: `tests/test_web.py` (`_app` gains a `plugin_records` pass-through), `tests/test_cli.py`

**Interfaces:**
- Consumes: `PluginRecord` (`name`, `version`, `module`, `status`, `error`) and `PluginRegistry.discover()` from spec zero; `Config.plugins.enabled`.
- Produces: `WebApp(..., plugin_records: Callable[[], list] | None = None)`; `GET /api/plugins` → `{"plugins": [{"name", "version", "module", "configured": bool, "running": str, "error": str | None}]}`; `PUT /api/plugins` JSON `{"name", "enabled": bool}` → the same shape plus `"restart_required": true`, 404 when enabling a name that is not installed, 400 on bad input.

- [ ] **Step 1: Write the failing tests**

In `tests/test_web.py` extend `_app` with `plugin_records=None` and the pass-through `if plugin_records is not None: kwargs["plugin_records"] = plugin_records`. Append:

```python
from richard.plugins.registry import PluginRecord


def _records():
    return [
        PluginRecord(name="home_assistant", version="1.0", module="richard.plugins.home_assistant:HomeAssistantPlugin", enabled=True, error="ValueError: host or token unset"),
        PluginRecord(name="reachy", version="0.1", module="richard_reachy:ReachyPlugin"),
    ]


def test_plugins_list_reports_configured_and_running_state(tmp_path):
    app = _app(tmp_path, plugin_records=_records)
    config = load_config(app._config_path)
    config.plugins.enabled = ["home_assistant"]
    save_config(config, app._config_path)
    rows = _body(app.handle("GET", "/api/plugins"))["plugins"]
    assert [(r["name"], r["configured"], r["running"]) for r in rows] == [("home_assistant", True, "error"), ("reachy", False, "disabled")]
    assert rows[0]["error"] == "ValueError: host or token unset"


def test_plugins_list_without_a_registry_discovers_installed_plugins(tmp_path):
    rows = _body(_app(tmp_path).handle("GET", "/api/plugins"))["plugins"]
    home = next(r for r in rows if r["name"] == "home_assistant")
    assert home["running"] == "unknown" and home["configured"] is False


def test_plugins_enable_and_disable_persist_and_ask_for_a_restart(tmp_path):
    app = _app(tmp_path, plugin_records=_records)
    data = _body(app.handle("PUT", "/api/plugins", json.dumps({"name": "reachy", "enabled": True}).encode()))
    assert data["restart_required"] is True
    assert load_config(app._config_path).plugins.enabled == ["reachy"]
    assert next(r for r in data["plugins"] if r["name"] == "reachy")["configured"] is True
    app.handle("PUT", "/api/plugins", json.dumps({"name": "reachy", "enabled": False}).encode())
    assert load_config(app._config_path).plugins.enabled == []


def test_plugins_enable_unknown_name_is_404_and_bad_body_is_400(tmp_path):
    app = _app(tmp_path, plugin_records=_records)
    assert app.handle("PUT", "/api/plugins", json.dumps({"name": "ghost", "enabled": True}).encode()).status == 404
    assert app.handle("PUT", "/api/plugins", b"[]").status == 400
    assert app.handle("PUT", "/api/plugins", json.dumps({"enabled": True}).encode()).status == 400
```

Append to `tests/test_cli.py`:

```python
def test_serve_passes_the_registry_records_to_the_web_app():
    import inspect

    source = inspect.getsource(cli._run_serve)
    assert "plugin_records=registry.records" in source
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_web.py tests/test_cli.py -q -k "plugins or registry_records"`
Expected: FAIL with `TypeError: WebApp.__init__() got an unexpected keyword argument 'plugin_records'`, 404s, and the source assertion

- [ ] **Step 3: Implement**

`WebApp.__init__`: add `plugin_records: Callable[[], list] | None = None,` and store `self._plugin_records = plugin_records`.

`_dispatch`, before `/api/restart`:

```python
        if method == "GET" and path == "/api/plugins":
            return self._list_plugins()
        if method == "PUT" and path == "/api/plugins":
            return self._update_plugin(body)
```

Handlers:

```python
    # --- plugins ---

    def _plugin_rows(self, config: Config) -> list[dict]:
        from richard.plugins.registry import PluginRegistry

        if self._plugin_records is not None:
            records = list(self._plugin_records())
            running_known = True
        else:
            records = PluginRegistry().discover()  # chat mode / tests: config state only
            running_known = False
        rows = []
        seen = set()
        for record in records:
            seen.add(record.name)
            rows.append({
                "name": record.name,
                "version": record.version,
                "module": record.module,
                "configured": record.name in config.plugins.enabled,
                "running": record.status if running_known else "unknown",
                "error": record.error,
            })
        for name in config.plugins.enabled:
            if name not in seen:
                rows.append({"name": name, "version": "?", "module": "?", "configured": True, "running": "missing", "error": None})
        return rows

    def _list_plugins(self) -> Response:
        config = self._load(self._config_path)
        return Response.json({"plugins": self._plugin_rows(config)})

    def _update_plugin(self, body: bytes) -> Response:
        try:
            payload = json.loads(body or b"{}")
        except (ValueError, TypeError) as exc:
            return Response.bad_request(f"invalid JSON: {exc}")
        if not isinstance(payload, dict) or not str(payload.get("name", "")).strip():
            return Response.bad_request("expected an object with a plugin name")
        name = str(payload["name"]).strip()
        enabled = _as_bool(payload.get("enabled", True))
        config = self._load(self._config_path)
        installed = {row["name"] for row in self._plugin_rows(config) if row["running"] != "missing"}
        if enabled and name not in installed:
            return Response.json({"error": f"plugin {name} is not installed"}, 404)
        names = [entry for entry in config.plugins.enabled if entry != name]
        if enabled:
            names.append(name)
        config.plugins.enabled = names
        self._save(config, self._config_path)
        return Response.json({"plugins": self._plugin_rows(config), "restart_required": True})
```

In `src/richard/cli.py` `_run_serve`, in the `WebApp(...)` call add `plugin_records=registry.records,` after `relays=relays,`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_web.py tests/test_cli.py -q`
Expected: all passed

- [ ] **Step 5: Full suite, then commit**

```bash
git add src/richard/web/app.py src/richard/cli.py tests/test_web.py tests/test_cli.py
git commit -m "web: /api/plugins shows installed plugins and toggles the enabled list

Running state comes from the serve process's registry; without one the page
shows configured state only. Changes ask for a restart, as agreed.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Voice page rebuilt (sections, remote-only block, sample upload, effect knobs)

**Files:**
- Modify: `src/richard/web/static.py` (CSS after `.empty-message`; the `data-drawer-page="voice"` section; the `<!-- system.voice.io -->` terminal-section, replaced whole; `FIELDS`; `SAVABLE`; `applyConfig`; `refreshAll`; the wiring block at the end)
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `/api/config` voice keys from Tasks 3–4; `/api/voices` from Task 6.
- Produces: page sections `voice-stt-content`, `voice-tts-content`, `voice-sample-content`, `voice-effect-content`, `voice-turn-content`, `voice-mic-content`; save sections `voice-stt`, `voice-tts`, `voice-effect`, `voice-turn`, `voice-mic`; JS `reflectRemoteOnly()`, `loadVoices()`, `useVoice(name)`, `uploadVoiceSample()`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_web.py`:

```python
def test_web_ui_voice_page_has_sections_upload_and_effect(tmp_path):
    html = _app(tmp_path).handle("GET", "/").body.decode()
    assert 'data-panel-content="voice-stt-content voice-tts-content voice-sample-content voice-effect-content voice-turn-content voice-mic-content"' in html
    for element_id in (
        "voice.language", "voice.tts_model", "voice.tts_language", "voice.tts_instructions", "voice.tts_xvec_only",
        "voice.tts_task_type", "voice.tts_effect", "voice.tts_effect_strength", "voice.tts_effect_tone",
        "voice.endpoint_silence_ms", "voice-sample-file", "voice-sample-name", "voice-sample-transcript",
        "voice-sample-consent", "voice-sample-upload", "voice-list", "tts-voice-options",
    ):
        assert f'id="{element_id}"' in html, element_id
    for section in ("voice-stt", "voice-tts", "voice-effect", "voice-turn", "voice-mic"):
        assert f'data-save="{section}"' in html
        assert f'id="{section}-status"' in html
    assert "data-remote-only" in html
    assert "/api/voices" in html
    assert "function reflectRemoteOnly()" in html
    assert "async function uploadVoiceSample()" in html
    assert 'id="voice-content"' not in html
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_web.py -q -k voice_page_has_sections`
Expected: FAIL on the `data-panel-content` assertion

- [ ] **Step 3: Rebuild the page**

CSS: after the `.empty-message { … }` rule add:

```css
  .remote-only-hidden { display: none !important; }
```

Drawer page: replace the voice page's body line with

```html
      <div class="drawer-page-body" data-panel-content="voice-stt-content voice-tts-content voice-sample-content voice-effect-content voice-turn-content voice-mic-content"></div>
```

Replace the whole `<!-- system.voice.io -->` terminal-section (from that comment to the `</div>` closing its `terminal-section`, just before `<!-- system.home_assistant.api -->`) with:

```html
    <!-- system.voice.stt -->
    <div class="terminal-section">
      <div class="terminal-header-line" data-target="voice-stt-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.voice.stt</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="voice-stt-content">
        <p class="lede">Speech-to-text. Remote points at a Whisper server; local runs faster-whisper in-process. Restart to apply.</p>
        <div class="field-row">
          <div class="setting-group"><label class="setting-label">STT engine</label><select id="voice.stt_engine" class="setting-input"><option value="local">local (faster-whisper)</option><option value="remote">remote (Whisper server)</option></select></div>
          <div class="setting-group"><label class="setting-label">STT model</label><input id="voice.stt_model" class="setting-input" placeholder="large-v3-turbo"></div>
        </div>
        <div class="field-row">
          <div class="setting-group"><label class="setting-label">STT endpoint (remote only)</label><input id="voice.stt_endpoint" class="setting-input" placeholder="http://host:8005"></div>
          <div class="setting-group"><label class="setting-label">Language</label><input id="voice.language" class="setting-input" placeholder="auto"><span class="lede" style="margin:0;">STT hint and TTS voice selection. auto = detect per utterance.</span></div>
        </div>
        <div class="button-row"><button class="setting-button" data-save="voice-stt">Save changes</button><div class="status-line" id="voice-stt-status"></div></div>
      </div>
    </div>

    <!-- system.voice.tts -->
    <div class="terminal-section">
      <div class="terminal-header-line" data-target="voice-tts-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.voice.tts</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="voice-tts-content">
        <p class="lede">Text-to-speech. Remote is the Qwen3-TTS server on the GPU box; kokoro and piper run in-process. Restart to apply.</p>
        <div class="field-row">
          <div class="setting-group"><label class="setting-label">TTS engine</label><select id="voice.tts_engine" class="setting-input"><option value="kokoro">kokoro</option><option value="piper">piper</option><option value="remote">remote (GPU server)</option></select></div>
          <div class="setting-group"><label class="setting-label">TTS voice</label><input id="voice.tts_voice" class="setting-input" list="tts-voice-options" placeholder="clap1"><datalist id="tts-voice-options"></datalist></div>
        </div>
        <div class="setting-group"><label class="setting-label">TTS endpoint (remote only)</label><input id="voice.tts_endpoint" class="setting-input" placeholder="http://host:8091"></div>
        <div class="setting-group"><div class="checkbox-btn"><input type="checkbox" id="voice.tts_streaming"><label for="voice.tts_streaming">Stream speech sentence-by-sentence</label><span class="checkmark"></span></div></div>
        <div data-remote-only>
          <div class="setting-group"><label class="setting-label">Qwen3-TTS request (remote only)</label><span class="lede" style="margin:0;">Fields sent to the server. Blank model = omit the field (vLLM-Omni serves one checkpoint).</span></div>
          <div class="field-row">
            <div class="setting-group"><label class="setting-label">Model</label><input id="voice.tts_model" class="setting-input" placeholder="(blank)"></div>
            <div class="setting-group"><label class="setting-label">Language</label><input id="voice.tts_language" class="setting-input" placeholder="Italian / English / (server default)"></div>
          </div>
          <div class="setting-group"><label class="setting-label">Instructions</label><input id="voice.tts_instructions" class="setting-input" placeholder="(none) style / emotion hint"></div>
          <div class="field-row">
            <div class="setting-group"><div class="checkbox-btn"><input type="checkbox" id="voice.tts_xvec_only"><label for="voice.tts_xvec_only">x-vector only (timbre only, native prosody)</label><span class="checkmark"></span></div></div>
            <div class="setting-group"><label class="setting-label">Task type</label><select id="voice.tts_task_type" class="setting-input"><option value="">(server default)</option><option value="Base">Base</option><option value="CustomVoice">CustomVoice</option><option value="VoiceDesign">VoiceDesign</option></select></div>
          </div>
        </div>
        <div class="button-row"><button class="setting-button" data-save="voice-tts">Save changes</button><div class="status-line" id="voice-tts-status"></div></div>
      </div>
    </div>

    <!-- system.voice.sample -->
    <div class="terminal-section" data-remote-only>
      <div class="terminal-header-line" data-target="voice-sample-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.voice.sample</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="voice-sample-content">
        <p class="lede">Upload a reference clip to the TTS server as a named voice. Mono, 8–15 s, speech only; the file goes up unchanged and the server resamples. Then pick it as the TTS voice above and save.</p>
        <div class="field-row">
          <div class="setting-group"><label class="setting-label">Audio file</label><input id="voice-sample-file" type="file" accept="audio/*" class="setting-input"></div>
          <div class="setting-group"><label class="setting-label">Voice name</label><input id="voice-sample-name" class="setting-input" placeholder="clap1v" maxlength="32"></div>
        </div>
        <div class="setting-group"><label class="setting-label">Transcript (optional; used by in-context mode, ignored by x-vector)</label><textarea id="voice-sample-transcript" class="setting-input" rows="2" placeholder="What the clip says"></textarea></div>
        <div class="setting-group"><div class="checkbox-btn"><input type="checkbox" id="voice-sample-consent"><label for="voice-sample-consent">I own this recording or have permission to use it</label><span class="checkmark"></span></div></div>
        <div class="button-row"><button class="setting-button" id="voice-sample-upload">Upload</button><div class="status-line" id="voice-sample-status"></div></div>
        <div class="setting-group" style="margin-top:0.85rem;">
          <label class="setting-label">Voices on the server</label>
          <div class="entry-list" id="voice-list"><div class="empty-message">Remote engine only.</div></div>
        </div>
      </div>
    </div>

    <!-- system.voice.effect -->
    <div class="terminal-section">
      <div class="terminal-header-line" data-target="voice-effect-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.voice.effect</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="voice-effect-content">
        <p class="lede">Post-processing on every spoken sentence, for any engine. Robot: speaker colouring, ring modulation and bit crush. Restart to apply.</p>
        <div class="field-row">
          <div class="setting-group"><label class="setting-label">Effect</label><select id="voice.tts_effect" class="setting-input"><option value="none">none</option><option value="robot">robot</option></select></div>
          <div class="setting-group"><label class="setting-label">Tone (Hz, 20–200)</label><input id="voice.tts_effect_tone" type="number" min="20" max="200" step="1" class="setting-input"></div>
        </div>
        <div class="setting-group"><label class="setting-label spread">Strength <span class="dial-value" id="voice.tts_effect_strength.val">—</span></label><input id="voice.tts_effect_strength" type="range" min="0" max="100" class="dial-slider"></div>
        <div class="button-row"><button class="setting-button" data-save="voice-effect">Save changes</button><div class="status-line" id="voice-effect-status"></div></div>
      </div>
    </div>

    <!-- system.voice.turns -->
    <div class="terminal-section">
      <div class="terminal-header-line" data-target="voice-turn-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.voice.turns</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="voice-turn-content">
        <p class="lede">When Richard decides you have finished speaking.</p>
        <div class="setting-group"><label class="setting-label">Realtime endpoint silence (ms)</label><input id="voice.endpoint_silence_ms" type="number" min="100" max="3000" step="50" class="setting-input"><span class="lede" style="margin:0;">Trailing silence that ends an utterance on the realtime API (Reachy, browser voice mode).</span></div>
        <div class="field-row">
          <div class="setting-group"><label class="setting-label">VAD aggressiveness (0–3)</label><input id="voice.vad_aggressiveness" type="number" min="0" max="3" class="setting-input"></div>
          <div class="setting-group"><label class="setting-label">Silence (ms)</label><input id="voice.silence_ms" type="number" min="0" class="setting-input"></div>
        </div>
        <span class="lede" style="margin:0;">VAD and silence apply to satellites and the local voice loop, not to the realtime API.</span>
        <div class="button-row"><button class="setting-button" data-save="voice-turn">Save changes</button><div class="status-line" id="voice-turn-status"></div></div>
      </div>
    </div>

    <!-- system.voice.mic -->
    <div class="terminal-section">
      <div class="terminal-header-line" data-target="voice-mic-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.voice.mic</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="voice-mic-content">
        <p class="lede">Only for <code>richard voice</code> on a machine with a microphone. serve does not use these.</p>
        <div class="field-row">
          <div class="setting-group"><label class="setting-label">Sample rate</label><input id="voice.samplerate" type="number" min="8000" class="setting-input"></div>
          <div class="setting-group"><label class="setting-label">Input device</label><input id="voice.input_device" class="setting-input" placeholder="(default)"></div>
        </div>
        <div class="setting-group"><label class="setting-label">Output device</label><input id="voice.output_device" class="setting-input" placeholder="(default)"></div>
        <div class="button-row"><button class="setting-button" data-save="voice-mic">Save changes</button><div class="status-line" id="voice-mic-status"></div></div>
      </div>
    </div>
```

FIELDS: replace every entry whose `sec` is `'voice'` with:

```js
  {id:'voice.stt_engine', path:['voice','stt_engine'], t:'sel', sec:'voice-stt'},
  {id:'voice.stt_model', path:['voice','stt_model'], t:'text', sec:'voice-stt'},
  {id:'voice.stt_endpoint', path:['voice','stt_endpoint'], t:'url', sec:'voice-stt', nullable:true},
  {id:'voice.language', path:['voice','language'], t:'text', sec:'voice-stt'},
  {id:'voice.tts_engine', path:['voice','tts_engine'], t:'sel', sec:'voice-tts'},
  {id:'voice.tts_voice', path:['voice','tts_voice'], t:'text', sec:'voice-tts'},
  {id:'voice.tts_endpoint', path:['voice','tts_endpoint'], t:'url', sec:'voice-tts', nullable:true},
  {id:'voice.tts_streaming', path:['voice','tts_streaming'], t:'bool', sec:'voice-tts'},
  {id:'voice.tts_model', path:['voice','tts_model'], t:'text', sec:'voice-tts'},
  {id:'voice.tts_language', path:['voice','tts_language'], t:'text', sec:'voice-tts'},
  {id:'voice.tts_instructions', path:['voice','tts_instructions'], t:'text', sec:'voice-tts'},
  {id:'voice.tts_xvec_only', path:['voice','tts_xvec_only'], t:'bool', sec:'voice-tts'},
  {id:'voice.tts_task_type', path:['voice','tts_task_type'], t:'sel', sec:'voice-tts'},
  {id:'voice.tts_effect', path:['voice','tts_effect'], t:'sel', sec:'voice-effect'},
  {id:'voice.tts_effect_strength', path:['voice','tts_effect_strength'], t:'dial', sec:'voice-effect'},
  {id:'voice.tts_effect_tone', path:['voice','tts_effect_tone'], t:'num', sec:'voice-effect', min:20, max:200},
  {id:'voice.endpoint_silence_ms', path:['voice','endpoint_silence_ms'], t:'num', sec:'voice-turn', min:100, max:3000},
  {id:'voice.vad_aggressiveness', path:['voice','vad_aggressiveness'], t:'num', sec:'voice-turn', min:0, max:3},
  {id:'voice.silence_ms', path:['voice','silence_ms'], t:'num', sec:'voice-turn', min:0},
  {id:'voice.samplerate', path:['voice','samplerate'], t:'num', sec:'voice-mic', min:8000},
  {id:'voice.input_device', path:['voice','input_device'], t:'text', sec:'voice-mic', nullable:true},
  {id:'voice.output_device', path:['voice','output_device'], t:'text', sec:'voice-mic', nullable:true},
```

`SAVABLE` becomes `['brain','personality','voice-stt','voice-tts','voice-effect','voice-turn','voice-mic','home-assistant','relay','web']`.

JS: add after `refreshHomeAssistant`:

```js
function reflectRemoteOnly(){
  const engine = $('voice.tts_engine');
  const remote = !!engine && engine.value === 'remote';
  document.querySelectorAll('[data-remote-only]').forEach(el => el.classList.toggle('remote-only-hidden', !remote));
}

async function loadVoices(){
  const options = $('tts-voice-options'); const box = $('voice-list');
  const engine = $('voice.tts_engine');
  if (!engine || engine.value !== 'remote') { options.innerHTML = ''; box.innerHTML = '<div class="empty-message">Remote engine only.</div>'; return; }
  try {
    const data = await getJSON('/api/voices');
    const uploaded = Object.fromEntries((data.uploaded || []).map(u => [u.name, u]));
    options.innerHTML = data.voices.map(v => '<option value="' + esc(v) + '"></option>').join('');
    box.innerHTML = data.voices.length ? data.voices.map(v => {
      const meta = uploaded[v] ? ('uploaded' + (uploaded[v].ref_text ? ' · ' + uploaded[v].ref_text.slice(0, 60) : '')) : 'built-in';
      return '<div class="entry"><div class="entry-main"><div class="entry-name">' + esc(v) + '</div><div class="entry-sub">' + esc(meta) + '</div></div>' +
        '<button class="setting-button small" data-use-voice="' + esc(v) + '">Use</button></div>';
    }).join('') : '<div class="empty-message">No voices on the server.</div>';
  } catch (e) {
    options.innerHTML = '';
    box.innerHTML = '<div class="empty-message">' + esc(e.message) + '</div>';
  }
}

function useVoice(name){
  const el = $('voice.tts_voice'); if (!el) return;
  el.value = name;
  el.dispatchEvent(new Event('input'));
}

function readFileBase64(file){
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
    reader.onerror = () => reject(reader.error || new Error('could not read the file'));
    reader.readAsDataURL(file);
  });
}

async function uploadVoiceSample(){
  const file = ($('voice-sample-file').files || [])[0];
  const name = $('voice-sample-name').value.trim();
  if (!file) { setStatus('voice-sample', 'choose an audio file', 'err'); return; }
  if (!/^[A-Za-z0-9_-]{1,32}$/.test(name)) { setStatus('voice-sample', 'name: letters, digits, _ and -, at most 32', 'err'); return; }
  if (!$('voice-sample-consent').checked) { setStatus('voice-sample', 'confirm you may use this recording', 'err'); return; }
  setStatus('voice-sample', 'uploading…', '');
  try {
    const audio_base64 = await readFileBase64(file);
    const data = await sendJSON('/api/voices', 'POST', {name, transcript: $('voice-sample-transcript').value.trim(), filename: file.name, audio_base64});
    setStatus('voice-sample', 'uploaded ' + data.uploaded + ' · ' + hm() + ' · selected above, save to use it', 'ok');
    await loadVoices();
    useVoice(data.uploaded);
  } catch (e) { setStatus('voice-sample', 'upload failed: ' + e.message, 'err'); }
}
```

In `applyConfig(cfg)` add `reflectRemoteOnly();` as the last statement before `renderReadout(cfg);`. In `refreshAll()` after `renderMemories(memories.memories);` add `try { await loadVoices(); } catch (e) {}`. In the wiring block, after the `$('home-assistant-restart')` line add:

```js
$('voice.tts_engine').addEventListener('change', () => { reflectRemoteOnly(); loadVoices(); });
$('voice-sample-upload').addEventListener('click', uploadVoiceSample);
$('voice-list').addEventListener('click', e => { const b = e.target.closest('[data-use-voice]'); if (b) useVoice(b.dataset.useVoice); });
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_web.py -q`
Expected: all passed

- [ ] **Step 5: Look at it once in a browser**

Run: `.venv/bin/python -c "from richard.web.static import SPA_HTML; open('/tmp/richard-spa.html','w').write(SPA_HTML)" && open /tmp/richard-spa.html`
Check: the drawer opens, Voice shows six windows, switching TTS engine to remote reveals the Qwen block and the sample window, the strength slider shows its value. The API calls fail (no server), that is expected.

- [ ] **Step 6: Full suite, then commit**

```bash
git add src/richard/web/static.py tests/test_web.py
git commit -m "web ui: Voice page regrouped, Qwen3-TTS fields, sample upload, effect knobs

Six windows with their own save buttons; the remote-only block and the sample
window hide unless the TTS engine is remote; the voice field suggests the
server's voices; strength is a dial, tone a number.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Plugins page, Network page, flat menu, wording

**Files:**
- Modify: `src/richard/web/static.py` (configuration nav; the `home-assistant`, `satellite`, `web-access` page sections; new `plugins-content` and `realtime-content` terminal-sections; the Home Assistant and serve ledes; `FIELDS`; `SAVABLE`; `refreshAll`; wiring)
- Test: `tests/test_web.py` (`test_web_ui_has_two_stage_drawer_and_all_page_destinations`, new test)

**Interfaces:**
- Consumes: `/api/plugins` from Task 7; `realtime` in the config API (Task 4).
- Produces: pages `plugins` (mounting `plugins-content home-assistant-content`) and `network` (mounting `realtime-content relay-content web-content`); save section `realtime`; JS `renderPlugins(rows)`, `loadPlugins()`, `togglePlugin(name, enabled)`.

- [ ] **Step 1: Update and add the tests**

In `tests/test_web.py::test_web_ui_has_two_stage_drawer_and_all_page_destinations` replace the page tuple with `("conversation", "automations", "memory", "system", "brain", "personality", "voice", "plugins", "network")` and add after the loop:

```python
    for gone in ("home-assistant", "satellite", "web-access"):
        assert f'data-drawer-page="{gone}"' not in html
    assert "05 PAGES" in html and "06 PAGES" not in html
```

Append:

```python
def test_web_ui_has_plugins_and_network_pages(tmp_path):
    html = _app(tmp_path).handle("GET", "/").body.decode()
    assert 'data-panel-content="plugins-content home-assistant-content"' in html
    assert 'data-panel-content="realtime-content relay-content web-content"' in html
    for element_id in ("plugins-list", "plugins-refresh", "plugins-restart", "plugins-status", "realtime.enabled", "realtime.host", "realtime.port", "realtime.token", "realtime-status"):
        assert f'id="{element_id}"' in html, element_id
    assert 'data-save="realtime"' in html
    assert "/api/plugins" in html
    assert "async function togglePlugin(name, enabled)" in html
    assert "plugin enable/disable" in html  # the serve lede lists what needs a restart
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_web.py -q -k "page_destinations or plugins_and_network"`
Expected: 2 failed

- [ ] **Step 3: Rework the drawer**

Configuration nav: replace the `drawer-title` span `06 PAGES` with `05 PAGES` and the six nav buttons with:

```html
        <button type="button" data-drawer-open="brain">Brain <span>LLM ›</span></button>
        <button type="button" data-drawer-open="personality">Personality <span>RICHARD ›</span></button>
        <button type="button" data-drawer-open="voice">Voice <span>STT + TTS ›</span></button>
        <button type="button" data-drawer-open="plugins">Plugins <span>CONNECTIONS ›</span></button>
        <button type="button" data-drawer-open="network">Network <span>REALTIME + RELAY + WEB ›</span></button>
```

Replace the three page sections `home-assistant`, `satellite`, `web-access` with:

```html
    <section class="drawer-page" data-drawer-page="plugins" hidden>
      <button type="button" class="drawer-back" data-drawer-back="configuration">‹ Back to configuration</button>
      <div class="drawer-title" tabindex="-1"><b>&gt; PLUGINS</b><span>system.plugins</span></div>
      <div class="drawer-page-body" data-panel-content="plugins-content home-assistant-content"></div>
    </section>

    <section class="drawer-page" data-drawer-page="network" hidden>
      <button type="button" class="drawer-back" data-drawer-back="configuration">‹ Back to configuration</button>
      <div class="drawer-title" tabindex="-1"><b>&gt; NETWORK</b><span>system.network</span></div>
      <div class="drawer-page-body" data-panel-content="realtime-content relay-content web-content"></div>
    </section>
```

Insert before `<!-- system.home_assistant.api -->`:

```html
    <!-- system.plugins.registry -->
    <div class="terminal-section">
      <div class="terminal-header-line" data-target="plugins-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.plugins.registry</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="plugins-content">
        <p class="lede">Connections Richard can use. Installed plugins come from Python entry points (richard plugins install). Enabling or disabling one takes effect after a restart.</p>
        <div class="entry-list" id="plugins-list"><div class="empty-message">Loading…</div></div>
        <div class="button-row"><button class="setting-button small" id="plugins-refresh">Refresh</button><button class="setting-button small" id="plugins-restart">Restart to apply</button><div class="status-line" id="plugins-status"></div></div>
      </div>
    </div>
```

Insert before `<!-- system.relay.mesh -->` (the terminal-section whose header targets `relay-content`):

```html
    <!-- system.realtime.api -->
    <div class="terminal-section">
      <div class="terminal-header-line" data-target="realtime-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.realtime.api</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="realtime-content">
        <p class="lede">The OpenAI-style realtime WebSocket the Reachy Conversation App and the browser voice mode connect to (wss://host:port/v1/realtime). Restart to apply.</p>
        <div class="setting-group"><div class="checkbox-btn"><input type="checkbox" id="realtime.enabled"><label for="realtime.enabled">Realtime API enabled</label><span class="checkmark"></span></div></div>
        <div class="field-row">
          <div class="setting-group"><label class="setting-label">Host</label><input id="realtime.host" class="setting-input"></div>
          <div class="setting-group"><label class="setting-label">Port</label><input id="realtime.port" type="number" class="setting-input"></div>
        </div>
        <div class="setting-group"><label class="setting-label">Token</label><input id="realtime.token" class="setting-input" placeholder="(open — set one to require it)" autocomplete="off"></div>
        <div class="button-row"><button class="setting-button" data-save="realtime">Save changes</button><div class="status-line" id="realtime-status"></div></div>
      </div>
    </div>
```

Wording: the Home Assistant lede becomes `Home Assistant plugin: entities and service calls. Use a long-lived access token from your Home Assistant profile. Save, test the connection, then restart Richard to apply.` The serve lede becomes `Restart the relay, realtime and web servers to apply changes that need it: host/port changes, STT/TTS engines and endpoints, the voice effect, personality, plugin enable/disable and plugin settings. Connections drop for a few seconds.`

FIELDS: add after the `web.port` entry:

```js
  {id:'realtime.enabled', path:['realtime','enabled'], t:'bool', sec:'realtime'},
  {id:'realtime.host', path:['realtime','host'], t:'text', sec:'realtime'},
  {id:'realtime.port', path:['realtime','port'], t:'port', sec:'realtime'},
  {id:'realtime.token', path:['realtime','token'], t:'text', sec:'realtime'},
```

`SAVABLE`: append `'realtime'`.

JS: add after `uploadVoiceSample`:

```js
function renderPlugins(rows){
  const box = $('plugins-list');
  if (!rows.length) { box.innerHTML = '<div class="empty-message">No plugins installed.</div>'; return; }
  box.innerHTML = rows.map(r => {
    const state = r.running === 'unknown' ? '' : ' · running: ' + esc(r.running) + (r.error ? ' (' + esc(r.error) + ')' : '');
    return '<div class="entry"><div class="entry-main"><div class="entry-name">' + esc(r.name) + ' <span class="entry-sub">' + esc(r.version) + '</span></div>' +
      '<div class="entry-sub">' + (r.configured ? 'enabled' : 'disabled') + state + '</div></div>' +
      '<button class="setting-button small" data-plugin-toggle="' + esc(r.name) + '" data-plugin-enabled="' + (r.configured ? '1' : '0') + '">' + (r.configured ? 'Disable' : 'Enable') + '</button></div>';
  }).join('');
}

async function loadPlugins(){
  try { renderPlugins((await getJSON('/api/plugins')).plugins); }
  catch (e) { $('plugins-list').innerHTML = '<div class="empty-message">' + esc(e.message) + '</div>'; }
}

async function togglePlugin(name, enabled){
  setStatus('plugins', (enabled ? 'enabling ' : 'disabling ') + name + '…', '');
  try {
    const data = await sendJSON('/api/plugins', 'PUT', {name, enabled});
    renderPlugins(data.plugins);
    setStatus('plugins', name + (enabled ? ' enabled' : ' disabled') + ' · restart to apply · ' + hm(), 'ok');
  } catch (e) { setStatus('plugins', 'failed: ' + e.message, 'err'); }
}
```

In `refreshAll()` after the `loadVoices()` line add `try { await loadPlugins(); } catch (e) {}`. Wiring, after the voice lines from Task 8:

```js
$('plugins-refresh').addEventListener('click', loadPlugins);
$('plugins-restart').addEventListener('click', rebootServe);
$('plugins-list').addEventListener('click', e => { const b = e.target.closest('[data-plugin-toggle]'); if (b) togglePlugin(b.dataset.pluginToggle, b.dataset.pluginEnabled !== '1'); });
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_web.py -q`
Expected: all passed

- [ ] **Step 5: Full suite, then commit**

```bash
git add src/richard/web/static.py tests/test_web.py
git commit -m "web ui: Plugins and Network pages, five-page configuration index

Home Assistant becomes the first plugin's settings under Plugins; realtime API,
relay and web access share one Network page. No third level anywhere.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Clean reference and render samples (Reachy folder, not Richard)

**Files:**
- Create: `/Users/matteo/Documents/GitHub/Reachy/voice-refs/claptrap/clean.sh`
- Create: `/Users/matteo/Documents/GitHub/Reachy/scripts/render_voice_samples.py`
- Output: `/Users/matteo/Documents/GitHub/Reachy/voice-refs/claptrap/clap1v.wav`, `clap1v.txt`; `/Users/matteo/Documents/GitHub/Reachy/samples/claptrap-v2/*.wav`

**Interfaces:**
- Consumes: `richard.voice.effects.RobotEffect` (Task 1) through the Richard worktree venv; the TTS server on CT123 at `http://10.99.97.202:8091` (`/v1/audio/voices`, `/v1/audio/speech`).
- Produces: voice `clap1v` on the server; sixteen sample files `{clap1,clap1v}_{plain,robot30,robot50,robot70}_{it,en}.wav`.

Announce in the session before step 3: "sending synthesis requests and one voice upload to the Qwen3-TTS server on CT123 (GPU1); CT111 untouched."

- [ ] **Step 1: Write `clean.sh`**

```bash
#!/usr/bin/env bash
# Separate the Claptrap promo vocals with demucs, cut the clap1 window (8.62–20.86 s) from
# the vocals stem, mono 24 kHz loudnorm, write clap1v.wav next to clap1.wav. Mac CPU, 1–2 min.
set -euo pipefail
cd "$(dirname "$0")"
VENV=../../.venv-demucs
if [ ! -x "$VENV/bin/demucs" ]; then
  uv venv -q "$VENV" --python 3.11
  uv pip install -q --python "$VENV/bin/python" demucs torch torchaudio
fi
"$VENV/bin/demucs" -n htdemucs --two-stems=vocals -o separated source.wav
VOC=separated/htdemucs/source/vocals.wav
ffmpeg -y -loglevel error -i "$VOC" -ss 8.62 -to 20.86 -ac 1 -ar 24000 \
  -af "loudnorm=I=-18:TP=-1.5:LRA=11" clap1v.wav
cp clap1.txt clap1v.txt
printf 'clap1v.wav: %s s\n' "$(ffprobe -v error -show_entries format=duration -of csv=p=0 clap1v.wav)"
```

Run: `chmod +x /Users/matteo/Documents/GitHub/Reachy/voice-refs/claptrap/clean.sh && /Users/matteo/Documents/GitHub/Reachy/voice-refs/claptrap/clean.sh`
Expected: `clap1v.wav: 12.24 s` (about). Listen once: speech with the music gone.

- [ ] **Step 2: Write `render_voice_samples.py`**

```python
#!/usr/bin/env python3
"""Render the Claptrap comparison set: plain and robot-effect variants of the same two
lines, for the voices given, from the Qwen3-TTS server. Run with the Richard worktree venv:

  /Users/matteo/Documents/GitHub/Richard/.worktrees/reachy-presence/.venv/bin/python \
    /Users/matteo/Documents/GitHub/Reachy/scripts/render_voice_samples.py --voices clap1 clap1v
"""
from __future__ import annotations

import argparse
import io
import wave
from pathlib import Path

import httpx

from richard.voice.effects import RobotEffect

LINES = {
    "it": ("Italian", "Ciao! Sono Reachy. Ho notato che il libro che cercavi ieri era sul tavolo della cucina. Vuoi che te lo ricordi piu tardi?"),
    "en": ("English", "Hi! I'm Reachy. I noticed the book you were looking for yesterday was on the kitchen table. Want me to remind you later?"),
}


def synth(client: httpx.Client, server: str, voice: str, language: str, text: str) -> tuple[bytes, int]:
    response = client.post(
        f"{server}/v1/audio/speech",
        json={"input": text, "voice": voice, "task_type": "Base", "language": language,
              "response_format": "wav", "x_vector_only_mode": True},
    )
    response.raise_for_status()
    with wave.open(io.BytesIO(response.content), "rb") as w:
        return w.readframes(w.getnframes()), w.getframerate()


def write_wav(path: Path, pcm: bytes, samplerate: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(samplerate)
        w.writeframes(pcm)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="http://10.99.97.202:8091")
    parser.add_argument("--voices", nargs="+", default=["clap1", "clap1v"])
    parser.add_argument("--strengths", nargs="+", type=int, default=[30, 50, 70])
    parser.add_argument("--tone", type=float, default=40.0)
    parser.add_argument("--out", default="/Users/matteo/Documents/GitHub/Reachy/samples/claptrap-v2")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(timeout=120.0)
    for voice in args.voices:
        for lang, (language, text) in LINES.items():
            pcm, samplerate = synth(client, args.server, voice, language, text)
            write_wav(out / f"{voice}_plain_{lang}.wav", pcm, samplerate)
            for strength in args.strengths:
                effect = RobotEffect(strength=strength, tone_hz=args.tone)
                write_wav(out / f"{voice}_robot{strength}_{lang}.wav", effect.process(pcm, samplerate), samplerate)
            print(f"{voice} {lang}: {len(pcm) / (2 * samplerate):.1f} s, {1 + len(args.strengths)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Upload the clean voice, then render**

Announce, then:

```bash
cd /Users/matteo/Documents/GitHub/Reachy/voice-refs/claptrap
curl -s -m 120 -X POST http://10.99.97.202:8091/v1/audio/voices \
  -F "audio_sample=@clap1v.wav" -F "consent=matteo-owner-2026-09-09" \
  -F "name=clap1v" -F "ref_text=$(cat clap1v.txt)"; echo
curl -s http://10.99.97.202:8091/v1/audio/voices | python3 -c "import sys,json; print(json.load(sys.stdin)['voices'])"
/Users/matteo/Documents/GitHub/Richard/.worktrees/reachy-presence/.venv/bin/python \
  /Users/matteo/Documents/GitHub/Reachy/scripts/render_voice_samples.py --voices clap1 clap1v
ls /Users/matteo/Documents/GitHub/Reachy/samples/claptrap-v2/
```

Expected: `clap1v` in the voice list; 16 wav files. Listen to `clap1_plain_it` against `clap1v_plain_it` for the noise, and the three robot strengths for character. Report the file list to Matteo; the choice is his. Nothing on CT123's Richard config changes in this task.

- [ ] **Step 4: Record**

Append one line to the CT123 `/root/CHANGELOG.md` through `rbox` ("2026-09-09: uploaded voice clap1v (demucs-cleaned clap1) to qwen3-tts; comparison samples rendered from the Mac"), then on the Mac `~/Documents/GitHub/inference-box/bin/pull-box-docs` and commit in the inference-box repo. Nothing to commit in Richard for this task.

---

### Task 11: Docs, spec amendments, deploy runbook

**Files:**
- Modify: `README.md` (Voice section), `docs/plugins.md` (Using plugins), the spec (sections 1 and 4)

- [ ] **Step 1: README**

In the Voice section add:

> **Voice effect.** `[voice] tts_effect = "robot"` post-processes every spoken sentence (speaker colouring, ring modulation, bit crush) on any engine; `tts_effect_strength` (0–100) and `tts_effect_tone` (Hz) tune it from the web UI's Voice page. **Voice samples.** With the remote engine, the Voice page uploads a reference clip to the TTS server as a named voice and lists the server's voices.

- [ ] **Step 2: docs/plugins.md**

After the `richard plugins ...` command block add: `The web UI's Plugins page lists the same plugins with their running state and toggles the enabled list; restart Richard to apply, as with the CLI.`

- [ ] **Step 3: Spec amendments**

In section 1 replace the gain-match bullet with: `**Makeup gain**: a fixed gain computed from the settings (compensating the ring modulator's RMS loss), never from the block, so chunk boundaries stay seamless; hard clip to int16.` In section 4 replace the token sentences with: `The token is shown like any other field; the config API already echoes it because the browser voice mode needs it to open the socket.` In section 8 replace `peak of the output equals the peak of the input` with `RMS within 3 dB of the input on a pass-band tone mix; output never leaves int16`.

- [ ] **Step 4: Commit and push**

```bash
git add README.md docs/plugins.md docs/superpowers/specs/2026-09-09-voice-effect-and-web-ui-refresh-design.md
git commit -m "docs: voice effect, sample upload, plugins page; spec amendments

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

Then the private-string check (memory `richard-public-push-check`) must print nothing, then `git push public reachy-presence`.

- [ ] **Step 5: Deploy, only on Matteo's go**

Restarting `richard.service` drops his live session. When he says go:

```bash
~/Documents/GitHub/inference-box/bin/rbox 'cd /opt/richard && git pull --ff-only && .venv/bin/pip install -e . -q && systemctl restart richard && sleep 8 && systemctl is-active richard && .venv/bin/richard plugins list'
```

Then one spoken turn through the web UI, then set the chosen voice and effect on CT123 (`richard config`, or the Voice page followed by Restart), one dated line in CT123's `/root/CHANGELOG.md`, `pull-box-docs`, commit in inference-box.

---

## Self-review against the spec

- **1 Voice effect**: Task 1 (`RobotEffect`, state, FIR tail, index-driven carrier, crush, fixed makeup gain, identity at 0), Task 2 (`EffectTTS`, config keys, builder wrap for every engine). ✓ Amendment: fixed makeup gain instead of per-block peak match, recorded in Task 11.
- **2 Chatterbox leftovers**: Task 3, with the grep that must print nothing. ✓
- **3 Voice page**: Task 8, six sections in the spec's order, remote-only block and sample window, voice list into a datalist, upload with consent, effect knobs, turn-taking, local microphone. Backend `/api/voices` in Task 6, `VoiceLibrary` in Task 5, name regex and 8 MB cap in Task 6. ✓
- **4 Realtime API**: Task 9 page; Task 4 adds the token to `PUT`. Token echo amendment in Task 11. ✓
- **5 Plugins**: Task 7 API with `plugin_records` from serve; Task 9 page with the Home Assistant form below the list. ✓
- **6 Menu**: Task 9, five pages, "05 PAGES", serve and Home Assistant ledes. ✓
- **7 Reference cleanup**: Task 10, demucs venv, cut, loudnorm, upload as `clap1v`, sixteen samples, Matteo picks. ✓
- **8 Tests**: effect (length, silence, identity, halves, loudness, range, rate change), wrapper, config round-trip and clamps, old keys absent and ignored, builder wrap, `/api/voices`, `/api/plugins`, realtime token, page markup. ✓
- **9 Deploy**: Task 11 step 5, gated. ✓
- **Placeholders**: none; every code step carries its code.
- **Type consistency**: `RobotEffect.process(pcm, samplerate)` used identically in Tasks 1, 2 and 10; `EffectTTS(inner, effect)` in Tasks 2 and cli; `VoiceLibrary.list()`/`upload(name, audio, filename, *, transcript, consent)` in Tasks 5 and 6 and the fake in test_web; `plugin_records: Callable[[], list]` in Tasks 7 and cli; section keys in FIELDS match the `data-save` values and `-status` ids in Tasks 8 and 9.
