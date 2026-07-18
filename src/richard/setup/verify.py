from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

import httpx


@dataclass(frozen=True)
class CheckResult:
    name: str
    url: str
    ok: bool
    detail: str


def _http_get_status(url: str) -> int:
    return httpx.get(url, timeout=5.0).status_code


def check_endpoints(
    targets: list[tuple[str, str]],
    http_get: Callable[[str], int] = _http_get_status,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    for name, url in targets:
        try:
            status = http_get(url)
            # "Reachable" = the service answered HTTP at all. A redirect (llama-swap's UI
            # 302) or a 404 (a shim with no root route) still means it's up and listening;
            # only a 5xx server error or a connection failure counts as down.
            ok = 200 <= status < 500
            results.append(CheckResult(name, url, ok, str(status)))
        except Exception as exc:  # network failures, timeouts, DNS, etc.
            results.append(CheckResult(name, url, False, str(exc)))
    return results


def wait_for_endpoints(
    targets: list[tuple[str, str]],
    http_get: Callable[[str], int] = _http_get_status,
    attempts: int = 1,
    delay: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
) -> list[CheckResult]:
    """Re-check until every endpoint is reachable or attempts run out. Services like
    Chatterbox load models after `systemctl start` returns, so the first probe can be
    premature — this gives them time to come up before the final report."""
    results = check_endpoints(targets, http_get=http_get)
    for _ in range(max(0, attempts - 1)):
        if all(r.ok for r in results):
            break
        sleep(delay)
        results = check_endpoints(targets, http_get=http_get)
    return results


def format_report(results: list[CheckResult]) -> str:
    lines = []
    for r in results:
        marker = "ok  " if r.ok else "FAIL"
        lines.append(f"[{marker}] {r.name:<6} {r.url} ({r.detail})")
    return "\n".join(lines)
