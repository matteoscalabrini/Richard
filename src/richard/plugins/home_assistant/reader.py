"""Home Assistant as a control-loop / diagnostics target source (kind "ha")."""
from __future__ import annotations

from richard.errors import HomeAssistantError
from richard.plugins.base import TargetInfo
from richard.plugins.home_assistant.client import HomeAssistantClient, HomeAssistantEntity
from richard.plugins.home_assistant.provider import entity_snapshot
from richard.verification import HOME_ASSISTANT, VerificationResult, failed, judge

_IGNORED_ATTRIBUTES = {"attribution", "entity_picture", "icon", "supported_features"}


def snapshot(entity: HomeAssistantEntity) -> dict:
    attributes = {k: v for k, v in entity.attributes.items() if k not in _IGNORED_ATTRIBUTES}
    return {"name": entity.name, "state": entity.state, "attributes": attributes}


class HomeAssistantTargetReader:
    def __init__(self, client: HomeAssistantClient) -> None:
        self._client = client

    def read(self, target_id: str) -> dict:
        return snapshot(self._client.get_entity(target_id))

    def list_targets(self) -> list[TargetInfo]:
        return [TargetInfo(id=e.entity_id, name=e.name) for e in self._client.list_entities()]

    def verify(self, target_id: str, expected: dict) -> VerificationResult:
        try:
            entity = self._client.get_entity(target_id)
        except HomeAssistantError as exc:
            return failed(HOME_ASSISTANT, f"ha:{target_id}", target_id, "verify", str(exc))

        def build(status, **kwargs):
            return VerificationResult(
                status=status, source=HOME_ASSISTANT, target=f"ha:{target_id}", name=entity.name,
                action="verify", requested=dict(expected), **kwargs,
            )

        return judge(build, expected, entity_snapshot(entity), entity.name)
