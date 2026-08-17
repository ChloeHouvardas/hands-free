# Docs

**Start here: [todos/main-sun-aug-16.md](todos/main-sun-aug-16.md)** — where the
project actually stands, what's left, and how to pick it up cold.

- [hardware-setup.md](hardware-setup.md) — flashing the Pi, camera, SSH, Pi Connect
- [field-notes/architecture.md](field-notes/architecture.md) — the system, and the order we build it in
- [field-notes/scope.md](field-notes/scope.md) — v1 goal and phases
- [field-notes/decisions.md](field-notes/decisions.md) — tech choices and why

# The code

All the code lives in `handsfree/`. The split that matters: nothing in the
gesture layer imports anything hardware, so it can be worked on from a laptop
with no Pi and no camera. It doesn't even need numpy — the whole gesture layer
is standard library only.

```
handsfree/
  __main__.py                                             the one entry point
  config.py  hand.py  filters.py  gestures.py  synth.py   no hardware, stdlib only
  hid.py  driver.py                                       no hardware either
  capture.py  landmarks.py  preview.py                    camera and MediaPipe
  transport/  null.py bluetooth.py usb.py                 where reports go out
  cli/                                                    one file per command
test_gestures.py  test_hid.py  test_driver.py             run them directly
config.toml                                               every threshold
handsfree.service                                         optional, run at boot
```

```
handsfree.config ← everything
handsfree.capture ← handsfree.landmarks ← cli.record, cli.bench
handsfree.hand, handsfree.filters ← handsfree.gestures ← cli.replay, test_gestures.py
handsfree.hid ← handsfree.driver ← cli.run, test_driver.py
handsfree.hid ← handsfree.transport.{null,bluetooth,usb} ← cli.run, cli.pair
```

Note where the line falls: `hid.py` and `driver.py` are on the **no hardware**
side. All the byte-level and timing logic is testable on a laptop; only
`transport/` opens anything.

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
| `handsfree/hid.py` | Report descriptors and report bytes. Pure; imports nothing. |
| `handsfree/driver.py` | Gesture events → input reports, the interpolation thread, and the guards that stop a mouse button being left held down. |
| `handsfree/transport/` | Where reports go: Bluetooth, USB, or `null` (prints them). |
| `handsfree/cli/` | The `main()` for each command. Everything that touches hardware lives here. |
| `test_gestures.py` | 36 tests over generated hands. Stays at the root so `python3 test_gestures.py` works. |
| `test_hid.py` | 18 tests. Parses each descriptor back and checks it describes the report we send. |
| `test_driver.py` | 25 tests, most of them asking "is the button up?" |

## Running it

On the Pi. The camera angle comes from `config.toml`, so no flag is needed.
Root, because the Bluetooth transport listens on privileged L2CAP ports:

```sh
sudo python3 -m handsfree pair --setup   # once per Pi
sudo python3 -m handsfree pair           # then pair from the Mac
sudo python3 -m handsfree run
```

`run` prints the live-view URL; the page reconnects itself when you restart.
Use `--no-preview` when the cursor matters — Wi-Fi and Bluetooth share one
antenna on a Pi 4. `--transport null` runs the whole chain without touching the
Mac, and needs no root.

On the Mac, with no hardware:

```sh
python3 test_gestures.py
python3 test_hid.py
python3 test_driver.py
python3 -m handsfree replay 'recordings/*.jsonl' --check
python3 -m handsfree replay 'recordings/*.jsonl' --check --drive   # + transport
```

Everything is a subcommand of `python3 -m handsfree`; run
`python3 -m handsfree` with no arguments for the list. Run it **from the repo
root** — `-m` puts the working directory on the import path, so from anywhere
else Python can't find the package. Running a file inside the package directly,
`python3 handsfree/cli/run.py`, does not work either, for the same reason.

## Things that will bite

- Only one process can hold the camera. "Device or resource busy" means an
  earlier run is still alive.
- picamera2's `"RGB888"` returns **BGR**-ordered arrays. If the preview looks
  right but tracking is bad, check this first.
- `mediapipe` is pinned to `0.10.18` — the last version with an aarch64 wheel.
- **macOS caches SDP records.** Change `hid.combined_descriptor` and a Mac that
  has already paired keeps the old one. Forget the device in System Settings
  and pair again, or you'll debug code that isn't running.
- **The Pi can no longer use Bluetooth mice or keyboards itself** — `pair
  --setup` disables BlueZ's `input` plugin to free the ports. It's a mouse now.
- Wi-Fi and Bluetooth are the same chip and antenna on a Pi 4. The MJPEG
  preview competes with the cursor.
