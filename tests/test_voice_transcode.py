"""to_reference_wav: normalize uploads to 24kHz mono WAV before they hit the TTS server.

Uses a fake ffmpeg (a tiny executable script) so the tests don't depend on real audio
codecs or a real ffmpeg install — they only check the command line and the module's
own response to ffmpeg's exit code / stdout.
"""
from __future__ import annotations

import shutil
import stat
import struct
import sys
from pathlib import Path

import pytest

from richard.voice.transcode import to_reference_wav
from richard.voice.voices import VoiceUploadError


def _wav_bytes(seconds: float, *, sample_rate: int = 24000, sample_width: int = 2) -> bytes:
    """A minimal (silent) 44-byte-header PCM WAV of the given duration."""
    n_samples = int(seconds * sample_rate)
    data = b"\x00" * (n_samples * sample_width)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + len(data), b"WAVE", b"fmt ", 16, 1, 1,
        sample_rate, sample_rate * sample_width, sample_width, sample_width * 8,
        b"data", len(data),
    )
    return header + data


def _write_fake_ffmpeg(tmp_path: Path, *, exit_code: int, stdout: bytes, stderr: str = "") -> Path:
    """A fake ffmpeg: records argv to a side file, echoes back a fixed WAV, and exits with the given code."""
    argv_file = tmp_path / "argv.txt"
    stdout_file = tmp_path / "stdout.bin"
    stdout_file.write_bytes(stdout)
    script = tmp_path / "fake_ffmpeg.py"
    script.write_text(
        "import sys\n"
        "sys.stdin.buffer.read()\n"
        f"open({str(argv_file)!r}, 'w').write(repr(sys.argv))\n"
        "if '-i' in sys.argv:\n"
        "    src = sys.argv[sys.argv.index('-i') + 1]\n"
        "    if src != 'pipe:0':\n"
        f"        open({str(argv_file) + '.input'!r}, 'wb').write(open(src, 'rb').read())\n"
        f"sys.stderr.write({stderr!r})\n"
        f"data = open({str(stdout_file)!r}, 'rb').read()\n"
        "sys.stdout.buffer.write(data)\n"
        f"sys.exit({exit_code})\n"
    )
    wrapper = tmp_path / "ffmpeg"
    wrapper.write_text(f"#!{sys.executable}\n" + script.read_text())
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return wrapper, argv_file


def test_runs_ffmpeg_with_the_expected_arguments(tmp_path):
    fake, argv_file = _write_fake_ffmpeg(tmp_path, exit_code=0, stdout=_wav_bytes(2.0))
    audio, filename = to_reference_wav(b"input-bytes", "clip.m4a", ffmpeg=str(fake))
    assert filename == "clip.wav"
    assert audio == _wav_bytes(2.0)
    argv = eval(argv_file.read_text())
    assert argv[0] == str(fake)
    assert argv[1:4] == ["-hide_banner", "-loglevel", "error"]
    assert argv[4] == "-i"
    assert argv[6:] == [
        "-t", "30.0", "-ac", "1", "-ar", "24000", "-sample_fmt", "s16", "-f", "wav", "pipe:1",
    ]


def test_feeds_ffmpeg_a_seekable_temp_file_not_stdin(tmp_path):
    """MP4/M4A put the moov index at the end of the file; ffmpeg cannot seek back on a
    pipe, exits 0 with a header-only WAV, and the upload is misreported as 'too short'.
    The input must reach ffmpeg as a real file (keeping the original suffix for probing),
    and that file must not outlive the call."""
    fake, argv_file = _write_fake_ffmpeg(tmp_path, exit_code=0, stdout=_wav_bytes(2.0))
    to_reference_wav(b"m4a-bytes", "samantha.m4a", ffmpeg=str(fake))
    argv = eval(argv_file.read_text())
    input_path = Path(argv[argv.index("-i") + 1])
    assert input_path.suffix == ".m4a"
    assert input_path.is_absolute()
    recorded = Path(str(argv_file) + ".input").read_bytes()
    assert recorded == b"m4a-bytes"
    assert not input_path.exists()


def test_rejects_a_clip_shorter_than_one_second(tmp_path):
    fake, _ = _write_fake_ffmpeg(tmp_path, exit_code=0, stdout=_wav_bytes(0.5))
    with pytest.raises(VoiceUploadError, match="too short"):
        to_reference_wav(b"input-bytes", "clip.wav", ffmpeg=str(fake))


def test_raises_voice_upload_error_on_nonzero_exit(tmp_path):
    fake, _ = _write_fake_ffmpeg(tmp_path, exit_code=1, stdout=b"", stderr="Could not decode audio file: garbage. Format not recognised.")
    with pytest.raises(VoiceUploadError, match="could not decode clip.mov"):
        to_reference_wav(b"input-bytes", "clip.mov", ffmpeg=str(fake))


def test_passes_through_unchanged_when_ffmpeg_is_not_available(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    audio, filename = to_reference_wav(b"raw-bytes", "clip.m4a")
    assert (audio, filename) == (b"raw-bytes", "clip.m4a")
