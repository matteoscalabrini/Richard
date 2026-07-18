"""Self-signed certificate for serving the config UI over HTTPS.

Browsers only allow microphone capture (`getUserMedia`) in a secure context — HTTPS or
`http://localhost`. The UI is reached over a LAN IP, so it needs HTTPS. We generate a
self-signed cert once (you accept the browser warning the first time). The relay port stays
plain ws for the hubs.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def ensure_self_signed_cert(cert_path, key_path) -> tuple[Path, Path]:
    """Return (cert, key), generating a self-signed pair via openssl if either is missing.

    Kept to a minimal, portable openssl invocation (works on both OpenSSL and LibreSSL).
    """
    cert_path = Path(cert_path)
    key_path = Path(key_path)
    if cert_path.exists() and key_path.exists():
        return cert_path, key_path
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key_path), "-out", str(cert_path),
            "-days", "3650", "-subj", "/CN=richard",
        ],
        check=True,
        capture_output=True,
    )
    return cert_path, key_path
