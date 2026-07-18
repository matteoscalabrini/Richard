from __future__ import annotations

import numpy as np


def resample_pcm16(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Resample mono 16-bit little-endian PCM from src_rate to dst_rate.

    Linear interpolation — adequate for speech downsampling on the host. Returns
    the input unchanged when the rates match.
    """
    if src_rate == dst_rate or len(pcm) == 0:
        return pcm
    src = np.frombuffer(pcm, dtype=np.int16)
    n_dst = int(round(len(src) * dst_rate / src_rate))
    if n_dst <= 0:
        return b""
    x_src = np.arange(len(src), dtype=np.float64)
    x_dst = np.linspace(0.0, len(src) - 1, n_dst)
    out = np.interp(x_dst, x_src, src.astype(np.float64))
    return np.clip(np.round(out), -32768, 32767).astype(np.int16).tobytes()
