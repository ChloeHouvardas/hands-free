# Docs

- [hardware-setup.md](hardware-setup.md) — flashing the Pi, camera, SSH, Pi Connect
- [field-notes/architecture.md](field-notes/architecture.md) — the system, and the order we build it in
- [field-notes/scope.md](field-notes/scope.md) — v1 goal and phases
- [field-notes/decisions.md](field-notes/decisions.md) — tech choices and why

# The code

Four files, flat by design. Dependency shape:

```
capture.py ← landmarks.py ← gestures.py
                         ← bench.py
```

Nothing here has run on the Pi yet. Treat the first pass as debugging, not
testing.

## `capture.py`

picamera2 only, no MediaPipe. Opens the camera at a given resolution and shows a
preview with an FPS counter.

Exists so that when something breaks later you can tell whether it's the camera
path or the ML. Exports `open_camera()`, reused by everything else.

```sh
python capture.py
python capture.py --width 320 --height 240
```

## `landmarks.py`

The core. `HandTracker` wraps camera + MediaPipe and exposes a `frames()`
generator yielding `(frame, landmarks)` — 21 points, or `None` when no hand is
visible. Tasks API, video mode, `num_hands=1`.

Also `draw()` for the skeleton and joints. Connections are hardcoded rather than
pulled from `mp.solutions`, which is the legacy API and expects a different
result type than the tasks API returns.

```sh
python landmarks.py
```

## `gestures.py`

Landmark geometry to events. `Pinch` measures thumb tip to index tip, divided by
wrist-to-middle-MCP so the threshold survives the hand moving nearer or further
from the camera.

Two thresholds, not one — enters below `0.35`, releases above `0.45` — so it
doesn't flicker when the distance sits on the boundary. Both values are guesses
and need tuning against a real camera.

Prints `PINCH_DOWN` / `PINCH_UP`. Releases rather than latching if the hand
leaves frame mid-pinch. No mouse output yet.

```sh
python gestures.py
python gestures.py --no-preview
```

## `bench.py`

The Phase 1 deliverable. Runs 640×480 and 320×240 for 30s each and prints FPS,
p50/p95 latency, CPU%, detection rate, and jitter.

Jitter is mean frame-to-frame movement of the index tip. Hold your hand still
for the run — the number that comes back is noise, and it's what decides whether
cursor control feels usable.

Run it **disconnected from Pi Connect screen share**; the desktop session is
real CPU on a 2GB Pi and will corrupt the results.

```sh
python bench.py
python bench.py --seconds 60
```

## Known risks in this code

- picamera2's `"RGB888"` format returns **BGR**-ordered arrays. `capture.py`
  depends on that for OpenCV; `landmarks.py` converts to RGB before MediaPipe.
  If the preview looks right but landmarks track badly, check this first.
- The pinch thresholds are untuned guesses.
