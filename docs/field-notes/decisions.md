# Decisions

| Feature | Tech | Why |
| --- | --- | --- |
| Real-time hand tracking | MediaPipe Hand Landmarker | Pretrained 21-landmark model plus the detect → crop → track pipeline around it. |
| Gesture classification | Landmark geometry | Pinch is a distance between two points; no model, no dataset, tunable by editing a constant. |
| Camera capture | picamera2 | Camera Module 3 is a CSI camera on the libcamera stack. |
| MediaPipe version | Pinned `0.10.18` | Last 0.10.x with an aarch64 Linux wheel; 0.10.20–0.10.35 are x86_64 only and won't install on a Pi. |
| Where inference runs | On the Pi | Keeps the host driverless and OS-agnostic. |
| Cursor smoothing | One Euro filter | Varies its cutoff with speed: steady when the hand is still, no added lag when it moves. A fixed low-pass can only trade one for the other. |
| Finger extension | Min of the PIP and DIP joint angles | Intrinsic to the finger, so it survives the camera being mounted sideways. Tip-vs-knuckle comparisons need a known "up". |
| Open palm vs three fingers | Pinky, with the thumb breaking ties | Pinky held to a stricter angle than the other fingers (166 vs 155): a swiping hand's pinky drifts to ~159, a spread palm sits at ~174. The thumb only votes inside that ambiguous band, and there PALM wins — parking is the clutch, so a park that doesn't take is worse than a swipe you repeat. |
| Taking control | Only a held POINT leaves PARKED | An idle hand sits with two or three fingers half out; letting those poses engage caused nearly every false click. |
| Scroll | Rate control from an anchor | Offset sets speed, not position, so a dropped frame costs a little speed rather than desyncing the gesture. |
| Swipe | Travel vs the furthest point in a rolling window | A fixed anchor resets mid-gesture and misses slow swipes; the window still rejects slow drift. |
| Thresholds | `config.toml`, in seconds and hand-sizes | Frame counts break when the rate wanders 8–12 fps; pixel distances break when the hand moves closer. |
| Gesture tuning | Recorded clips + generated hands | Recordings keep it honest about real sloppy input; `synth.py` keeps it from overfitting to one afternoon's hands. |
| Code layout | `handsfree/` package at the root, with shim scripts | Root holds what you run, the package holds what you import. Not `src/` — that layout requires installing the project to run it, and this one is deployed by `git pull` and run in place. |
