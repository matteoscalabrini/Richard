"""Ambient perception (spec 3b): frames in, events out, snapshots on request.

Sensor (motion, person, face) → gate (debounce, transitions, cooldowns, quiet
hours) → brain (context items, control-loop events, the camera tool). Pillow and
onnxruntime are imported lazily inside this package only: the lean core stays
numpy-only and `richard serve` starts without them unless the plugin is enabled.
"""
