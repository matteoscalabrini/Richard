"""Home Assistant as a Richard plugin: tools, the "ha" target kind, one context line."""
from __future__ import annotations

from collections.abc import Callable

from richard.config import HomeAssistant
from richard.plugins.base import PluginContext, PluginParts
from richard.plugins.home_assistant.client import HomeAssistantClient
from richard.plugins.home_assistant.provider import HomeAssistantProvider
from richard.plugins.home_assistant.reader import HomeAssistantTargetReader

KIND = "ha"


class HomeAssistantPlugin:
    name = "home_assistant"
    version = "1.0"

    def __init__(self, client_factory: Callable[..., HomeAssistantClient] = HomeAssistantClient) -> None:
        self._client_factory = client_factory

    def config_defaults(self) -> dict:
        return HomeAssistant().to_table()

    def build(self, ctx: PluginContext) -> PluginParts:
        settings = HomeAssistant.from_table(ctx.config, enabled=True)
        if not settings.host or not settings.token:
            raise ValueError("host or token unset; configure both before restarting Richard")
        client = self._client_factory(
            settings.url, settings.token, timeout=settings.timeout, verify_ssl=settings.verify_ssl,
        )
        return PluginParts(
            providers=[HomeAssistantProvider(client)],
            target_readers={KIND: HomeAssistantTargetReader(client)},
            context=f"Home Assistant is connected at {settings.url}.",
        )
