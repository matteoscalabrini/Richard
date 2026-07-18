import shutil
import ssl

import pytest

from richard.web.tls import ensure_self_signed_cert


def test_ensure_self_signed_cert_creates_loadable_cert(tmp_path):
    if shutil.which("openssl") is None:
        pytest.skip("openssl not available")
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    ensure_self_signed_cert(cert, key)
    assert cert.exists() and key.exists()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)  # must not raise


def test_ensure_self_signed_cert_is_idempotent(tmp_path):
    if shutil.which("openssl") is None:
        pytest.skip("openssl not available")
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    ensure_self_signed_cert(cert, key)
    first = cert.read_bytes()
    ensure_self_signed_cert(cert, key)  # should not regenerate
    assert cert.read_bytes() == first
