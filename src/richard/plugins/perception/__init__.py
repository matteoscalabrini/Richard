"""Ambient perception as a built-in plugin: disabled means invisible to the model."""
from __future__ import annotations

from richard.plugins.base import PluginContext, PluginParts

CONTEXT = ("Ambient perception is on: you are told when someone arrives, leaves or is recognised, "
           "and you can look at the live camera with the camera tool.")


def _default_detector(settings, write):
    from richard.perception.detect import PersonDetector

    return PersonDetector.load(device=settings.device, threads=4, write=write, threshold=settings.person_threshold)


def _default_identifier(settings, gallery, write):
    if not settings.identity_enabled or gallery is None:
        return None
    from richard.perception.faces import FaceDetector, FaceEmbedder, FaceIdentifier

    return FaceIdentifier(FaceDetector.load(device=settings.device, write=write),
                          FaceEmbedder.load(device=settings.device, write=write), gallery)


class PerceptionPlugin:
    name = "perception"
    version = "0.1"

    def __init__(self, *, detector_factory=None, identifier_factory=None) -> None:
        self._detector_factory = detector_factory or _default_detector
        self._identifier_factory = identifier_factory or _default_identifier
        self.service = None

    def config_defaults(self) -> dict:
        return {"identity_enabled": False, "quiet_hours": "", "sensitivity": 50, "cooldown_s": 120,
                "enter_debounce_s": 2, "leave_debounce_s": 10, "stream_fps": 2, "keep_thumbnails": False,
                "device": "cpu", "stale_s": 5}

    def build(self, ctx: PluginContext) -> PluginParts:
        from richard.perception.camera import CameraProvider, PresenceProvider
        from richard.perception.pipeline import PerceptionService, Settings
        from richard.perception.reader import PerceptionTargetReader

        settings = Settings.from_table(ctx.config)
        detector = self._detector_factory(settings, ctx.write)
        service = PerceptionService(settings, data_dir=ctx.data_dir, person_detector=detector, write=ctx.write)
        service._identifier = self._identifier_factory(settings, service.gallery, ctx.write)
        service.start()
        self.service = service
        return PluginParts(
            providers=[CameraProvider(service), PresenceProvider(service)],
            target_readers={"perception": PerceptionTargetReader(service)},
            event_sources=[service.event_source],
            context=CONTEXT,
            shutdown=service.stop,
        )
