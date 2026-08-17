# Docs

**Start here: [todos/main-sun-aug-16.md](todos/main-sun-aug-16.md)** — where the
project actually stands, what's left, and how to pick it up cold.

- [hardware-setup.md](hardware-setup.md) — flashing the Pi, camera, SSH, Pi Connect
- [field-notes/architecture.md](field-notes/architecture.md) — the system, and the order we build it in
- [field-notes/scope.md](field-notes/scope.md) — v1 goal and phases
- [field-notes/decisions.md](field-notes/decisions.md) — tech choices and why

# The code

**The root holds what you run; `handsfree/` holds what you import.** The split
that matters: nothing in the gesture layer imports anything hardware, so it can
be worked on from a laptop with no Pi and no camera. It doesn't even need
numpy — the whole gesture layer is standard library only.

```
handsfree/
  config.py  hand.py  filters.py  gestures.py  synth.py   no hardware, stdlib only
  capture.py  landmarks.py  preview.py                    camera and MediaPipe
  cli/                                                    one file per command
gestures.py  record.py  replay.py  bench.py               shims: `python3 gestures.py`
capture.py   landmarks.py  test_gestures.py
config.toml                                               every threshold
```

```
handsfree.config ← everything
handsfree.capture ← handsfree.landmarks ← cli.record, cli.bench
handsfree.hand, handsfree.filters ← handsfree.gestures ← cli.replay, test_gestures.py
```

| Module | What it does |
| --- | --- |
| `handsfree/config.py` + `config.toml` | Every threshold, and the camera mounting angle. |
| `handsfree/capture.py` | Camera only, no ML — so it's obvious which half broke. |
| `handsfree/landmarks.py` | Camera + MediaPipe → 21 landmarks, or `None`. |
| `handsfree/preview.py` | Streams the annotated view to a browser; the Pi is headless. |
| `handsfree/hand.py` | Pure geometry: joint angles, pinch ratio, hand size. Imports nothing. |
| `handsfree/filters.py` | One Euro filter — steady when still, no lag when moving. |
| `handsfree/gestures.py` | The state machine. Its docstring is the best explanation of the design. |
| `handsfree/synth.py` | Builds hands from scratch, for tests that don't need a camera. |
| `handsfree/cli/` | The `main()` for each command. Everything that touches hardware lives here. |
| `test_gestures.py` | 36 tests over generated hands. Stays at the root so `python3 test_gestures.py` works. |

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

Run the commands from the repo root, by their root-level name. The scripts at
the root are three-line shims into `handsfree/cli/`; running a module inside the
package directly — `python3 handsfree/gestures.py` — does **not** work, because
Python then puts `handsfree/` on the import path instead of the repo root.

## Things that will bite

- Only one process can hold the camera. "Device or resource busy" means an
  earlier run is still alive.
- picamera2's `"RGB888"` returns **BGR**-ordered arrays. If the preview looks
  right but tracking is bad, check this first.
- `mediapipe` is pinned to `0.10.18` — the last version with an aarch64 wheel.
