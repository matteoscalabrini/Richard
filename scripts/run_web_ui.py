#!/usr/bin/env python3
"""Standalone launcher for Richard's web config UI — no voice extras or LLM needed.

Runs only the web server (no satellite relay) against the real on-disk config and
databases, so you can poke at the UI locally. Usage:

    .venv/bin/python scripts/run_web_ui.py [--host 127.0.0.1] [--port 8771]
"""
from __future__ import annotations

import argparse
import asyncio

from richard.config import default_config_path, load_config
from richard.control_loops import (
    ControlLoopStore,
    ControlTargetReader,
    default_control_loops_path,
)
from richard.devices import DeviceRegistry, default_devices_path
from richard.home_assistant import HomeAssistantClient
from richard.memory import MemoryStore, default_memory_path
from richard.satellite.relays import RelayRegistry
from richard.web import WebApp, serve_web


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Richard's web config UI standalone")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8771)
    args = parser.parse_args()

    config = load_config()
    registry = DeviceRegistry(default_devices_path())
    ha = config.home_assistant
    home_assistant = None
    if ha.enabled and ha.host and ha.token:
        home_assistant = HomeAssistantClient(
            ha.url, ha.token, timeout=ha.timeout, verify_ssl=ha.verify_ssl
        )
    app = WebApp(
        config_path=default_config_path(),
        registry=registry,
        memory_store=MemoryStore(default_memory_path()),
        control_loop_store=ControlLoopStore(default_control_loops_path()),
        control_target_reader=ControlTargetReader(
            registry, home_assistant=home_assistant
        ),
        relays=RelayRegistry(),
    )
    print(f"Richard web UI at http://{args.host}:{args.port}/  (Ctrl-C to stop)")
    print(f"  config: {default_config_path()}")
    print(f"  devices: {default_devices_path()}")
    print(f"  memory: {default_memory_path()}")
    print(f"  control loops: {default_control_loops_path()}")
    try:
        asyncio.run(serve_web(app, args.host, args.port))
    except KeyboardInterrupt:
        print("\nbye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
