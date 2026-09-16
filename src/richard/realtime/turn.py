"""Semantic end-of-turn detection with smart-turn v3.2 (ONNX).

Complements EndpointDetector's silence-based endpointing: instead of always
waiting out a fixed trailing-silence window, ask a small classifier whether
the utterance sounds *complete* (falling intonation, finished sentence) so
turns can end as soon as ~160 ms after the speaker stops, while a longer
ceiling (~1200 ms) still catches genuine pauses mid-thought.
"""
from __future__ import annotations

import numpy as np

SAMPLE_RATE = 16000
WINDOW_SAMPLES = 128000  # 8 s at 16 kHz — smart-turn's fixed input window


class SmartTurn:
    """Wraps the smart-turn-v3.2 ONNX model: PCM16 bytes -> completion probability."""

    def __init__(self, onnx_path, *, session=None, extractor=None) -> None:
        self._model_path = str(onnx_path)
        self._session = session
        self._extractor = extractor

    def _ensure_session(self):
        if self._session is None:
            import onnxruntime

            options = onnxruntime.SessionOptions()
            options.execution_mode = onnxruntime.ExecutionMode.ORT_SEQUENTIAL
            options.intra_op_num_threads = 1
            options.graph_optimization_level = (
                onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
            )
            self._session = onnxruntime.InferenceSession(
                self._model_path, sess_options=options, providers=["CPUExecutionProvider"]
            )
        return self._session

    def _ensure_extractor(self):
        if self._extractor is None:
            from transformers import WhisperFeatureExtractor

            self._extractor = WhisperFeatureExtractor(chunk_length=8, feature_size=80)
        return self._extractor

    def is_complete(self, pcm16: bytes) -> float:
        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        if audio.shape[0] > WINDOW_SAMPLES:
            audio = audio[-WINDOW_SAMPLES:]
        extractor = self._ensure_extractor()
        features = extractor(
            audio,
            sampling_rate=SAMPLE_RATE,
            return_tensors="np",
            padding="max_length",
            max_length=WINDOW_SAMPLES,
            truncation=True,
            do_normalize=True,
        )["input_features"].astype(np.float32)
        outputs = self._ensure_session().run(None, {"input_features": features})
        return float(np.asarray(outputs[0]).reshape(-1)[0])
