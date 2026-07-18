from richard.setup.verify import (
    CheckResult,
    check_endpoints,
    format_report,
    wait_for_endpoints,
)


def test_wait_retries_until_reachable():
    calls = {"n": 0}

    def flaky(url):
        calls["n"] += 1
        # down for the first two probes of this single target, then up
        return 200 if calls["n"] >= 3 else 503

    slept = []
    results = wait_for_endpoints(
        [("TTS", "http://x")], http_get=flaky, attempts=5, delay=2, sleep=slept.append
    )
    assert results[0].ok is True
    assert calls["n"] == 3  # stopped retrying as soon as it came up
    assert slept == [2, 2]  # waited between the three probes


def test_wait_gives_up_after_attempts():
    results = wait_for_endpoints(
        [("TTS", "http://x")], http_get=lambda u: 503, attempts=3, delay=0, sleep=lambda d: None
    )
    assert results[0].ok is False


def test_check_marks_2xx_ok_and_errors_down():
    def fake_get(url):
        return 200 if "good" in url else 503

    results = check_endpoints([("LLM", "http://good"), ("TTS", "http://bad")], http_get=fake_get)
    assert results == [
        CheckResult("LLM", "http://good", True, "200"),
        CheckResult("TTS", "http://bad", False, "503"),
    ]


def test_reachable_redirect_or_404_counts_as_up():
    # A 302 (llama-swap UI redirect) or 404 (shim with no root route) means the
    # service is listening — reachable, not down. Only 5xx is a real failure.
    results = check_endpoints(
        [("LLM", "http://redir"), ("STT", "http://noroute"), ("X", "http://err")],
        http_get=lambda url: {"http://redir": 302, "http://noroute": 404, "http://err": 500}[url],
    )
    assert [r.ok for r in results] == [True, True, False]


def test_check_handles_unreachable():
    def fake_get(url):
        raise OSError("connection refused")

    results = check_endpoints([("LLM", "http://x")], http_get=fake_get)
    assert results[0].ok is False
    assert "connection refused" in results[0].detail


def test_format_report_shows_status_per_line():
    report = format_report([
        CheckResult("LLM", "http://x", True, "200"),
        CheckResult("TTS", "http://y", False, "down"),
    ])
    assert "ok" in report and "FAIL" in report
    assert "LLM" in report and "TTS" in report
