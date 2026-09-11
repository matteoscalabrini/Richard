"""One bounded background preparation queue owned by the web service."""
from __future__ import annotations

import threading
from copy import deepcopy

from richard.realtime.cues import cue_fingerprint, cue_languages, prepare_cues, read_cues


class _Superseded(Exception):
    pass


class CuePreparation:
    def __init__(self, directory, *, can_prepare=lambda: True):
        self.directory = directory
        self._can_prepare = can_prepare
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._thread = None
        self._pending = None
        self._generation = 0
        self._key = None
        self._force = False
        self._status = {"state": "missing", "completed": 0, "total": 0}

    @staticmethod
    def _identity(config):
        return tuple(cue_fingerprint(config, lang) for lang in cue_languages(config))

    def status(self, config):
        languages = cue_languages(config)
        key = self._identity(config)
        with self._lock:
            if key == self._key and self._status["state"] in {"queued", "preparing", "error"}:
                return {**self._status, "languages": list(languages)}
        ready = sum(len(read_cues(config, self.directory, language=lang)["clips"]) for lang in languages)
        total = 6 * len(languages)
        return {"state": "unsupported" if not languages else "ready" if ready == total else "missing",
                "completed": ready, "total": total, "languages": list(languages)}

    def request(self, config, *, force=False):
        snapshot = deepcopy(config)
        key = self._identity(snapshot)
        with self._lock:
            if not key or self._closed.is_set():
                return self.status(snapshot)
            if (key == self._key and self._status["state"] in {"queued", "preparing"}
                    and (not force or self._force)):
                return self.status(snapshot)
            self._generation += 1
            self._key, self._force = key, force
            self._pending = (snapshot, force, self._generation)
            self._status = {"state": "queued", "completed": 0, "total": 6 * len(key)}
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True, name="cue-preparation")
                self._thread.start()
            return self.status(snapshot)

    def _checkpoint(self, generation):
        while True:
            with self._lock:
                if self._closed.is_set() or generation != self._generation:
                    raise _Superseded()
                allowed = self._can_prepare()
                self._status["state"] = "preparing" if allowed else "queued"
            if allowed:
                return
            self._closed.wait(0.25)

    def _run(self):
        while True:
            with self._lock:
                job, self._pending = self._pending, None
                if job is None or self._closed.is_set():
                    self._thread = None
                    return
            config, force, generation = job
            try:
                for index, language in enumerate(cue_languages(config)):
                    self._checkpoint(generation)
                    def progress(completed, offset=index * 6):
                        with self._lock:
                            if generation == self._generation:
                                self._status["completed"] = offset + completed
                    prepare_cues(config, self.directory, force=force, language=language,
                                 before_clip=lambda: self._checkpoint(generation), progress=progress)
                with self._lock:
                    if generation == self._generation:
                        self._status["state"] = "ready"
            except _Superseded:
                pass
            except Exception:
                with self._lock:
                    if generation == self._generation:
                        self._status.update(state="error", error="Could not prepare waiting phrases. Check the TTS service and retry.")

    def close(self):
        self._closed.set()
        with self._lock:
            self._pending = None
            thread = self._thread
        if thread is not None:
            thread.join(timeout=2)
