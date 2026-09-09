"""Presence as control-loop targets (kind "perception"): `perception:person_present`,
`perception:identified:<name>`. Loops can watch them like Home Assistant entities."""
from __future__ import annotations

from richard.plugins.base import TargetInfo
from richard.verification import VerificationResult, failed, judge

PERCEPTION = "perception"
PRESENT = "person_present"


class PerceptionTargetReader:
    def __init__(self, service) -> None:
        self._service = service

    def _snapshot(self, target_id: str) -> dict:
        present = [p["subject"] for p in self._service.presence()]
        if target_id == PRESENT:
            return {"name": "Someone present", "state": "on" if present else "off", "attributes": {"present": present}}
        if target_id.startswith("identified:"):
            name = target_id.split(":", 1)[1]
            return {"name": f"{name} present", "state": "on" if name in present else "off", "attributes": {}}
        raise KeyError(target_id)

    def read(self, target_id: str) -> dict:
        return self._snapshot(target_id)

    def list_targets(self) -> list[TargetInfo]:
        names = [p["name"] for p in self._service.gallery.list()] if self._service.gallery else []
        return [TargetInfo(id=PRESENT, name="Someone present")] + [
            TargetInfo(id=f"identified:{n}", name=f"{n} present") for n in names]

    def verify(self, target_id: str, expected: dict) -> VerificationResult:
        try:
            snap = self._snapshot(target_id)
        except KeyError:
            return failed(PERCEPTION, f"perception:{target_id}", target_id, "verify", "unknown target")

        def build(status, **kwargs):
            return VerificationResult(status=status, source=PERCEPTION, target=f"perception:{target_id}",
                                      name=snap["name"], action="verify", requested=dict(expected), **kwargs)

        return judge(build, expected, snap, snap["name"])
