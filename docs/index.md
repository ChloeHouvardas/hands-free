# Docs

**Start here: [todos/main-sun-aug-16.md](todos/main-sun-aug-16.md)** — where the
project actually stands, what's left, and how to pick it up cold.

- [hardware-setup.md](hardware-setup.md) — flashing the Pi, camera, SSH, Pi Connect
- [field-notes/architecture.md](field-notes/architecture.md) — the system, and the order we build it in
- [field-notes/scope.md](field-notes/scope.md) — v1 goal and phases
- [field-notes/decisions.md](field-notes/decisions.md) — tech choices and why

# The code

Flat by design. The split that matters: nothing in the gesture layer imports
anything hardware, so it can be worked on from a laptop with no Pi and no
camera.

```
config.py  ← everything
capture.py ← landmarks.py ← record.py, bench.py
hand.py, filters.py ← gestures.py ← replay.py, test_gestures.py
```

| File | What it does |
| --- | --- |
| `config.py` / `config.toml` | Every threshold, and the camera mounting angle. |
| `capture.py` | Camera only, no ML — so it's obvious which half broke. |
| `landmarks.py` | Camera + MediaPipe → 21 landmarks, or `None`. |
| `preview.py` | Streams the annotated view to a browser; the Pi is headless. |
| `hand.py` | Pure geometry: joint angles, pinch ratio, hand size. Imports nothing. |
| `filters.py` | One Euro filter — steady when still, no lag when moving. |
| `gestures.py` | The state machine. Its docstring is the best explanation of the design. |
| `synth.py` | Builds hands from scratch, for tests that don't need a camera. |
| `record.py` / `replay.py` | Record landmark sessions, replay them offline. |
| `test_gestures.py` | 36 tests over generated hands. |
| `bench.py` | FPS, latency, CPU, jitter. |

## Running it

On the Pi. The camera angle comes from `config.toml`, so no flag is needed:

```sh
python gestures.py
```

It prints the live-view URL; the page reconnects itself when you restart.

On the Mac, with no hardware:

```sh
python3 test_gestures.py
python3 replay.py 'recordings/*.jsonl' --check
```

## Things that will bite

- Only one process can hold the camera. "Device or resource busy" means an
  earlier run is still alive.
- picamera2's `"RGB888"` returns **BGR**-ordered arrays. If the preview looks
  right but tracking is bad, check this first.
- `mediapipe` is pinned to `0.10.18` — the last version with an aarch64 wheel.
