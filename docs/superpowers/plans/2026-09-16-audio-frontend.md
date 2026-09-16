# Audio front end: STT language restriction and semantic end-of-turn Implementation Plan (round 2, plan C of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop Whisper from hallucinating Turkish and Korean on hesitations, and stop the 400 ms silence rule from chopping sentences (19 of 52 turns cancelled on 2026-09-16).

**Architecture:** `TurnTranscriber` gains an allowed-language set and per-segment filtering. `EndpointDetector` gains a semantic turn predictor (Pipecat smart-turn v3.2, 8 MB ONNX, BSD-2, 23 languages incl. Italian) consulted during trailing silence between a floor and a ceiling; Silero VAD stays for speech start/stop. Model file follows the Silero convention (`~/.richard/models/`). Branch `reachy-presence`. Runs after Plans A and B. Ends with the deploy of all three plans to CT123.

**Tech Stack:** faster-whisper 1.2.1 (`WhisperModel.detect_language` available), onnxruntime 1.29 (present on the CT), numpy, `transformers` (new, for `WhisperFeatureExtractor`; numpy-only path, no torch).

## Global Constraints

- `voice.language = "auto"` keeps meaning "detect", but detection is restricted to `voice.languages` (default `["it", "en"]`).
- Endpointing defaults: floor 160 ms, ceiling 1200 ms, predictor threshold 0.5, predictor run at most every 160 ms of trailing silence. With no predictor available the detector behaves exactly as today (`silence_ms`).
- New model download uses the `ensure_silero` pattern (`realtime/vad.py:32-49`).
- Commit after each task with the message given. Deployment only in Task C3 with the listed commands.

---

### Task 1: Whisper restricted to Italian and English, hallucinated segments dropped (item 3)

**Files:**
- Modify: `src/richard/realtime/stt.py` (`TurnTranscriber.__init__(model_name, language=None, languages=("it", "en"), ...)`, `_decode`), `src/richard/config.py` (`Voice.languages: list[str]` default `["it", "en"]`, load/save), `src/richard/cli.py:842-848` (pass `languages=config.voice.languages`)
- Test: `tests/test_realtime_stt.py`, `tests/test_config.py`

**Interfaces:**
- `_decode(pcm)`: if `self._language` is None and `self._languages`: call `model.detect_language(audio)` → `(language, probability, all_probs)`; pick the allowed language with the highest probability from `all_probs` (a list of `(code, prob)`), default to the first allowed when none is listed; pass it as `language=` to `transcribe`. Segments with `no_speech_prob > 0.6` or `avg_logprob < -1.0` are dropped (attributes read with `getattr(..., default)` so fakes without them pass). Return `Transcription(text, chosen_language)`.

- [ ] **Step 1: Failing tests** (extend `tests/test_realtime_stt.py`; its `FakeModel` records `transcribe` kwargs and returns segments):
```python
def test_auto_language_is_restricted_to_allowed_set():
    model = FakeModel([FakeSegment("ciao")])
    model.detect_language = lambda audio: ("tr", 0.51, [("tr", 0.51), ("it", 0.30), ("en", 0.10)])
    t = TurnTranscriber("base", language=None, languages=("it", "en"), model=model)
    out = t.final_with_language(b"\x00" * 3200)
    assert out.language == "it"
    assert model.calls[-1][1]["language"] == "it"


def test_hallucinated_segments_are_dropped():
    segs = [FakeSegment("İzlediğiniz için teşekkür ederim.", no_speech_prob=0.9, avg_logprob=-0.4),
            FakeSegment("come va", no_speech_prob=0.1, avg_logprob=-0.3),
            FakeSegment("garbage", no_speech_prob=0.1, avg_logprob=-1.5)]
    model = FakeModel(segs)
    t = TurnTranscriber("base", language="it", model=model)
    assert t.final(b"\x00" * 3200) == "come va"
```
(Give `FakeSegment` optional `no_speech_prob=0.0, avg_logprob=0.0` fields; check how `TurnTranscriber` receives its model in the existing tests, the fixture at stt.py:24-66 uses `_ensure()`; if there is no `model=` seam, add one as an optional kwarg used by `_ensure`.)
Config: `languages = ["it", "en"]` default; round-trip.
- [ ] **Step 2: Run** → failures.
- [ ] **Step 3: Implement** as in Interfaces. `detect_language` in faster-whisper 1.2 takes the float32 audio array (same one passed to `transcribe`) and returns `(language, language_probability, all_language_probs)`; wrap in try/except and fall back to `language=self._languages[0]` if it raises.
- [ ] **Step 4: Run** the two test files + suite.
- [ ] **Step 5: Commit** `stt: detect only allowed languages; drop hallucinated segments`

---

### Task 2: Semantic end-of-turn with smart-turn v3.2 (item 2)

**Files:**
- Create: `src/richard/realtime/turn.py`
- Modify: `src/richard/realtime/vad.py` (`EndpointDetector`: kwargs `turn_predictor=None, min_silence_ms=160, max_silence_ms=1200, predictor_every_ms=160, threshold=0.5`; `ensure_smart_turn()`), `src/richard/config.py` (`Voice.turn_detector: str = "smart"` (`smart|silence`), `Voice.endpoint_max_silence_ms: int = 1200`), `src/richard/cli.py:481-483, 841-848` (build the predictor when `turn_detector == "smart"`, pass it), `src/richard/setup/deployment.py:21-33` (pre-download), `pyproject.toml` (`transformers>=4.40` in the `voice` extra)
- Test: `tests/test_realtime_turn.py` (new), `tests/test_realtime_vad.py`

**Interfaces:**
- `turn.SmartTurn(onnx_path, *, session=None, extractor=None)`; `is_complete(pcm16: bytes) -> float`: int16 → float32/32768, keep the last 8 s (128000 samples), `WhisperFeatureExtractor(chunk_length=8, feature_size=80)` with `sampling_rate=16000, return_tensors="np", padding="max_length", max_length=128000, truncation=True, do_normalize=True`; run ONNX input `input_features` (float32, shape from the extractor); return `float(outputs[0][0])`. Session options: sequential execution, 1 inter-op thread, ORT_ENABLE_ALL.
- `vad.ensure_smart_turn(models_dir=None, client=None, write=print) -> Path` downloading `https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/main/smart-turn-v3.2-cpu.onnx` to `smart-turn-v3.2-cpu.onnx` (8,679,182 bytes) with the `_present` guard.
- `EndpointDetector.feed`: in the in-speech branch, when `self._trailing >= self._min_silence_frames` and a predictor is set, call it at most every `predictor_every_ms` of trailing silence on `bytes(self._collected)[-256000:]`; end the utterance when the score `>= threshold` or when `self._trailing >= self._max_silence_frames`; without a predictor use `silence_ms` as today. The `("utterance", pcm)` emission and reset stay identical.

- [ ] **Step 1: Failing tests**
`tests/test_realtime_turn.py`: with a fake ONNX session whose `run` records the input name/shape and returns `[[0.83]]`, and a fake extractor returning a `(1, 80, 800)` float32 array, `SmartTurn(...).is_complete(b"\x00\x01" * 16000)` returns `0.83` and the session was called with key `"input_features"`. Also: input longer than 8 s is truncated to the last 128000 samples (assert on the array passed to the extractor).
`tests/test_realtime_vad.py` (using the file's `ScriptedVAD`/`_detector` helper with `frame_ms=32`): with `turn_predictor` returning `[0.1, 0.9]` in order, `min_silence_ms=64` (2 frames), `max_silence_ms=320`, `predictor_every_ms=64`: after speech frames then silence frames, the utterance is emitted right after the predictor's second call (the 0.9), before the ceiling; with a predictor that always returns `0.1`, the utterance is emitted at the ceiling (10 frames); with `turn_predictor=None`, behaviour equals today's `silence_ms` test.
Download test mirroring the Silero one with a `FakeClient`.
- [ ] **Step 2: Run** → failures.
- [ ] **Step 3: Implement** `turn.py`, the detector changes, `ensure_smart_turn`, config fields, cli wiring (`predictor = SmartTurn(ensure_smart_turn(write=write)) if config.voice.turn_detector == "smart" else None`, built once and shared, `EndpointDetector(vad_factory(), silence_ms=..., turn_predictor=predictor, max_silence_ms=config.voice.endpoint_max_silence_ms)`), deployment pre-download, pyproject extra. Import `transformers` lazily inside `SmartTurn.__init__` so the package stays optional; a missing import logs a warning and `cli.py` falls back to `None` (silence mode).
- [ ] **Step 4: Run** the test files, the suite, and one real inference on the Mac: `.venv/bin/python -c "from richard.realtime.turn import SmartTurn; from richard.realtime.vad import ensure_smart_turn; import numpy as np; s=SmartTurn(ensure_smart_turn()); print(s.is_complete(np.zeros(32000, dtype=np.int16).tobytes()))"` must print a float (requires `.venv/bin/pip install transformers` or `uv pip install --python .venv/bin/python transformers` locally first; note the wall time of the call, expected well under 100 ms after warm-up).
- [ ] **Step 5: Commit** `realtime: semantic end-of-turn with smart-turn v3.2 between a 160 ms floor and a 1200 ms ceiling`

---

### Task 3: Deploy plans A, B, C to CT123 and clean the stores (item 5 + rollout)

Preconditions: all commits of the three plans on `reachy-presence`, `.venv/bin/pytest -q` green, `node --test tests/browser/*.test.cjs` green, the private-string grep from the memory note richard-public-push-check is empty, `git diff b6a2fd4..HEAD | grep -E '^\+.*10\.(99|77)\.'` empty, Tavily key present at `/root/.richard/tavily.key` on the CT (Matteo's step).

- [ ] **Step 1:** `git push public reachy-presence`.
- [ ] **Step 2:** on the CT: backup config (`cp /root/.richard/config.toml /root/work/misc/config.toml.bak-$(date +%Y%m%d)-round2`), add `timezone = "Europe/Rome"` at top level, `languages = ["it", "en"]`, `turn_detector = "smart"`, `endpoint_max_silence_ms = 1200` under `[voice]`, `weather_entity = ""` under `[plugins.home_assistant]` (only if a weather entity name is known; else leave unset), then `cd /opt/richard && git pull --ff-only && uv pip install --python .venv/bin/python -e ".[voice]" && .venv/bin/richard plugins enable web_search` (or the equivalent `richard plugins enable` invocation; check `richard --help`).
- [ ] **Step 3:** memory clean-up with Matteo's confirmed list (48 and 49 as of 2026-09-16): `sqlite3 /root/.richard/memory.db "delete from memories where id in (48,49)"` before the restart so the head is rebuilt without them.
- [ ] **Step 4:** `systemctl restart richard`; wait for `:8766`, `:8771`; `journalctl -u richard --since "-2 min"` must show the smart-turn model loaded and the web_search plugin enabled (no "disabled (...)" line).
- [ ] **Step 5:** smoke: three typed turns via `/root/work/scripts/realtime_smoke.py` ("che ore sono?", "che tempo fa domani?", "chi ha scritto Merrily We Roll Along?") and read the replies + timing lines; then Matteo talks; pull `turn user:` / `turn reply:` / `turn timing:` lines and the cancelled-turn ratio for the research note.
- [ ] **Step 6:** CT changelog line, `pull-box-docs`, commit in inference-box; update `reachy-lab/RESEARCH-FAST-DUPLEX-2026-09-16.md` with the new cancelled ratio and `stt`/`first_token` numbers; memory update.

## Self-review
- Items covered: 3 → C1, 2 → C2, 5 + rollout → C3.
- `transformers` is a new optional dependency; the fallback keeps today's behaviour if it is missing.
