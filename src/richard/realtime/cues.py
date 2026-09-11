"""Prepare and read the small, same-voice cue bank used by realtime clients.

Preparation is an explicit offline action. Runtime readers only accept a complete
bank matching the current voice configuration and otherwise return silence.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path

from richard.config import Config, default_config_path, load_config

_SCHEMA = 1
_CATALOG_REVISION = 2
_CACHE_NAME = "realtime-cues"
_MANIFEST_NAME = "manifest.json"
_MIN_SAMPLE_RATE = 8_000
_MAX_SAMPLE_RATE = 192_000
_MAX_CLIP_SECONDS = 30

_CATALOGS = {
    "en": (
        ("thinking-0", "thinking", "Mm, let me think."),
        ("thinking-1", "thinking", "One moment."),
        ("thinking-2", "thinking", "Let me work that out."),
        ("visual-0", "visual", "Let me take a look."),
        ("visual-1", "visual", "I'm checking the image."),
        ("visual-2", "visual", "Let me see."),
    ),
    "it": (
        ("thinking-0", "thinking", "Mmh, fammi pensare."),
        ("thinking-1", "thinking", "Un momento."),
        ("thinking-2", "thinking", "Ci sto pensando."),
        ("visual-0", "visual", "Fammi dare un'occhiata."),
        ("visual-1", "visual", "Controllo l'immagine."),
        ("visual-2", "visual", "Vediamo."),
    ),
}


def _selected_language(config: Config) -> str:
    configured = str(getattr(config.voice, "language", "") or "").strip().lower()
    if configured in {"", "auto", "en", "english"}:
        return "en"
    if configured in {"it", "italian"}:
        return "it"
    return configured


def cue_languages(config: Config) -> tuple[str, ...]:
    """Auto prepares both supported languages, without guessing the next turn."""
    if str(config.voice.language or "").strip().lower() in {"", "auto"}:
        return ("en", "it")
    selected = _selected_language(config)
    return (selected,) if selected in _CATALOGS else ()


def _catalog_payload(language: str) -> list[dict[str, str]]:
    return [
        {"id": clip_id, "phase": phase, "text": text}
        for clip_id, phase, text in _CATALOGS.get(language, ())
    ]


def _tts_settings(config: Config) -> dict:
    """Return all current and future ``tts_*`` settings as fingerprint input."""
    return {
        key: value
        for key, value in vars(config.voice).items()
        if key.startswith("tts_")
    }


def cue_fingerprint(config, language: str) -> str:
    """Opaque identity of the selected catalog and every TTS-affecting setting."""
    selected = str(language or "").strip().lower()
    if selected in {"", "auto", "english"}:
        selected = "en"
    elif selected == "italian":
        selected = "it"
    material = {
        "catalog_revision": _CATALOG_REVISION,
        "catalog": _catalog_payload(selected),
        "language": selected,
        "tts": _tts_settings(config),
    }
    encoded = json.dumps(
        material, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _cache_directory(directory) -> Path:
    if directory is not None:
        return Path(directory)
    return default_config_path().parent / _CACHE_NAME


def _empty_bank(config: Config, language: str) -> dict:
    return {
        "fingerprint": cue_fingerprint(config, language),
        "language": language,
        "clips": [],
    }


def _valid_audio(pcm: bytes, sample_rate: object) -> bool:
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, int):
        return False
    if not (_MIN_SAMPLE_RATE <= sample_rate <= _MAX_SAMPLE_RATE):
        return False
    return 0 < len(pcm) <= sample_rate * _MAX_CLIP_SECONDS * 2 and len(pcm) % 2 == 0


def _decode_clip(raw: object, expected: dict[str, str]) -> dict | None:
    if not isinstance(raw, dict):
        return None
    if any(raw.get(key) != value for key, value in expected.items()):
        return None
    try:
        pcm = base64.b64decode(raw.get("audio", ""), validate=True)
    except (ValueError, TypeError, binascii.Error):
        return None
    sample_rate = raw.get("sample_rate")
    if not _valid_audio(pcm, sample_rate):
        return None
    byte_length = raw.get("byte_length")
    if isinstance(byte_length, bool) or not isinstance(byte_length, int):
        return None
    if byte_length != len(pcm):
        return None
    if raw.get("sha256") != hashlib.sha256(pcm).hexdigest():
        return None
    return {
        **expected,
        "audio": base64.b64encode(pcm).decode("ascii"),
        "sample_rate": sample_rate,
    }


def read_cues(config, directory=None, *, language=None) -> dict:
    """Read the matching prepared bank, returning an empty bank on any defect."""
    language = language or _selected_language(config)
    empty = _empty_bank(config, language)
    catalog = _catalog_payload(language)
    if not catalog:
        return empty
    path = _cache_directory(directory) / (empty["fingerprint"] + ".json")
    if not path.exists():
        path = _cache_directory(directory) / _MANIFEST_NAME
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            return empty
        if manifest.get("schema") != _SCHEMA:
            return empty
        if manifest.get("catalog_revision") != _CATALOG_REVISION:
            return empty
        if manifest.get("fingerprint") != empty["fingerprint"]:
            return empty
        if manifest.get("language") != language:
            return empty
        raw_clips = manifest.get("clips")
        if not isinstance(raw_clips, list) or len(raw_clips) != len(catalog):
            return empty
        clips = []
        for raw, expected in zip(raw_clips, catalog, strict=True):
            clip = _decode_clip(raw, expected)
            if clip is None:
                return empty
            clips.append(clip)
    except (OSError, ValueError, TypeError):
        return empty
    return {**empty, "clips": clips}


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_cues(config, directory=None, *, synth=None, force=False, language=None,
                 before_clip=None, progress=None) -> dict:
    """Synthesize and atomically publish the configured cue bank."""
    language = language or _selected_language(config)
    catalog = _catalog_payload(language)
    if not catalog:
        return _empty_bank(config, language)

    destination = _cache_directory(directory)
    if not force:
        existing = read_cues(config, destination, language=language)
        if existing["clips"]:
            if progress is not None:
                progress(len(existing["clips"]))
            return existing

    if before_clip is not None:
        before_clip()
    if synth is None:
        from richard.cli import _build_tts

        synthesis_config = deepcopy(config)
        synthesis_config.voice.tts_language = {"en": "English", "it": "Italian"}[language]
        synth = _build_tts(synthesis_config, print)

    clips = []
    for entry in catalog:
        if before_clip is not None:
            before_clip()
        pcm = synth.synth(entry["text"])
        sample_rate = synth.samplerate
        if not isinstance(pcm, bytes):
            pcm = bytes(pcm)
        if not _valid_audio(pcm, sample_rate):
            raise ValueError(f"TTS returned invalid PCM16 for {entry['id']}")
        clips.append(
            {
                **entry,
                "audio": base64.b64encode(pcm).decode("ascii"),
                "sample_rate": sample_rate,
                "byte_length": len(pcm),
                "sha256": hashlib.sha256(pcm).hexdigest(),
            }
        )
        if progress is not None:
            progress(len(clips))

    fingerprint = cue_fingerprint(config, language)
    manifest = {
        "schema": _SCHEMA,
        "catalog_revision": _CATALOG_REVISION,
        "fingerprint": fingerprint,
        "language": language,
        "clips": clips,
    }
    if before_clip is not None:
        before_clip()
    current = destination / (fingerprint + ".json")
    _atomic_write_json(current, manifest)
    # Keep a few recent voice/language combinations, not an ever-growing archive.
    variants = sorted((p for p in destination.glob("*.json")
                       if re.fullmatch(r"[0-9a-f]{64}\.json", p.name) and p != current),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    for expired in variants[7:]:
        expired.unlink(missing_ok=True)
    return read_cues(config, destination, language=language)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare Richard realtime voice cues")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Prepare the configured voice cue bank")
    prepare.add_argument("--config", type=Path, help="Config file (default: ~/.richard/config.toml)")
    prepare.add_argument("--language", choices=("en", "it", "all"),
                         help="Override the configured language; auto prepares English and Italian")
    prepare.add_argument(
        "--force",
        action="store_true",
        help="Replace the bank even when its voice fingerprint still matches",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config_path = args.config or default_config_path()
    config = load_config(config_path)
    languages = (("en", "it") if args.language == "all" else
                 (args.language,) if args.language else cue_languages(config))
    for language in languages:
        bank = prepare_cues(config, Path(config_path).parent / _CACHE_NAME,
                            force=args.force, language=language)
        print(f"Prepared {len(bank['clips'])} realtime voice cues ({bank['language']}).")
    if not languages:
        print(f"No realtime voice cues for configured language {_selected_language(config)!r}.")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
