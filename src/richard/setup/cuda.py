"""Locate CUDA runtime libraries installed in the Chatterbox environment."""
from __future__ import annotations

import argparse
from pathlib import Path


def cuda_library_path(venv: Path | str) -> str:
    """Return the cuBLAS/cuDNN library path supplied by a Python venv."""
    root = Path(venv)
    paths: list[Path] = []
    for package in ("cublas", "cudnn"):
        candidates = root.glob(
            f"lib/python*/site-packages/nvidia/{package}/lib"
        )
        paths.extend(path for path in sorted(candidates) if path.is_dir())
    return ":".join(str(path) for path in paths)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("venv", help="Python environment containing NVIDIA packages")
    args = parser.parse_args(argv)
    print(cuda_library_path(args.venv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
