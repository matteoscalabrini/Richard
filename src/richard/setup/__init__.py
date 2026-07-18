from __future__ import annotations

from pathlib import Path
from typing import Callable

from richard.config import load_config, save_config
from richard.setup.detect import HardwareProfile, detect_hardware
from richard.setup.plan import apply_plan, plan_install
from richard.setup.verify import check_endpoints, format_report


def run_setup(
    config_path: Path | None = None,
    detect: Callable[[], HardwareProfile] = detect_hardware,
    configure_llm: Callable[[], tuple[str, str | None, str]] | None = None,
    provision: Callable[..., None] | None = None,
    check: Callable[[list[tuple[str, str]]], list] = check_endpoints,
    gpu: str = "0",
    override: str | None = None,
    write: Callable[[str], None] = print,
) -> int:
    """detect -> plan -> configure LLM -> apply+save -> provision (GPU) -> verify."""
    profile = detect()
    write(
        f"Detected: GPU={profile.has_gpu} VRAM={profile.vram_mb}MB "
        f"CPU={profile.cpu_cores} RAM={profile.ram_mb}MB"
    )
    plan = plan_install(profile, override=override)
    write(f"Plan: {plan.rung} (services={'yes' if plan.needs_services else 'no'})")

    config = load_config(config_path)
    apply_plan(plan, config)
    if configure_llm is not None:
        endpoint, api_key, model = configure_llm()
        config.llm_endpoint = endpoint
        config.llm_api_key = api_key
        config.llm_model = model
    save_config(config, config_path)
    write("Wrote config.")

    if plan.needs_services and provision is not None:
        write("Provisioning voice services…")
        provision(plan, write)

    # Probe real, cheap routes: the LLM's model list and Chatterbox's UI root.
    targets = [("LLM", config.llm_endpoint.rstrip("/") + "/v1/models")]
    if plan.needs_services:
        targets.append(("TTS", plan.tts_endpoint))
    results = check(targets)
    if results:
        write(format_report(results))
    return 0


__all__ = ["run_setup"]
