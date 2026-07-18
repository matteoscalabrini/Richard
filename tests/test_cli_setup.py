import pytest

from richard.cli import _build_parser, main


def test_setup_subcommand_parses_flags():
    args = _build_parser().parse_args(
        ["setup", "--llm-url", "http://x:8080", "--llm-key", "sk", "--llm-model", "M",
         "--profile", "cpu", "--non-interactive"]
    )
    assert args.command == "setup"
    assert args.llm_url == "http://x:8080"
    assert args.llm_key == "sk"
    assert args.llm_model == "M"
    assert args.profile == "cpu"
    assert args.non_interactive is True


def test_profile_choices_restricted():
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["setup", "--profile", "bogus"])


def test_main_dispatches_to_run_setup(monkeypatch):
    called = {}

    def fake_run_setup(**kwargs):
        called.update(kwargs)
        return 0

    monkeypatch.setattr("richard.cli.run_setup", fake_run_setup, raising=False)
    rc = main(["setup", "--profile", "cpu", "--non-interactive", "--llm-url", "http://x"])
    assert rc == 0
    assert called["override"] == "cpu"
