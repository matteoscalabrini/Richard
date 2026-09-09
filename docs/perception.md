# Ambient perception

Richard watches continuously and speaks rarely. Three stages:

1. **Sensor.** Frame differencing (numpy) for activity and scene changes; SSD-MobileNetV1
   (ONNX model zoo, MIT) for people; UltraFace (MIT) + ArcFace int8 (Apache-2.0) for
   opt-in recognition against a local gallery. Models download to `~/.richard/models` on
   first use (about 95 MB in total).
2. **Gate.** Debounce (2 s to enter, 10 s to leave, two agreeing matches to be named),
   transitions only, a cooldown per event and person, quiet hours, a master switch.
3. **Brain.** An admitted event becomes a `[perception] HH:MM ...` line in the open realtime
   conversation before its next turn, or a control-loop event (`perception:person_present`,
   `perception:identified:<name>`) when nobody is talking. Richard can look himself with
   `camera(question, detail, region)`: low = 800 px, high = the source's native frame,
   region = a crop at native resolution (`left|right|centre|top|bottom` or `x0,y0,x1,y1`).
   `who_is_here` and `last_seen(name)` answer from the presence log.

Enable: `richard plugins enable perception`, install the extra
(`pip install "richard-companion[perception]"`), restart, open the web UI's Perception page
and allow the camera. The browser streams 800 px JPEG frames at 2 fps while the tab is
visible; the header shows "◉ camera" while it does. Everything stays on the box; the
gallery is enrolled from the page and deletable there.

Config (`[plugins.perception]`): `identity_enabled`, `quiet_hours`, `sensitivity` (0-100),
`cooldown_s`, `enter_debounce_s`, `leave_debounce_s`, `stream_fps`, `keep_thumbnails`,
`device` (`cpu`|`cuda`), `stale_s`.

Logs: `perception: <event>` and `perception: dropped <kind> (<reason>)` on the `richard.perception`
logger. Robot source and wake-ups when nobody is talking: spec four and spec one.
