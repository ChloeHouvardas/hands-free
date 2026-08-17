"""Where frames come from. The camera is only one of the answers.

`run.py` needs a very small thing from a frame source: a `frames()` generator
yielding `(frame, landmarks)`, and a `close()`. That's the entire contract —
`record.py`, `bench.py` and `cli/landmarks.py` use nothing more either. So the
camera can be swapped for a recording or for generated hands, and the rest of
the app cannot tell.

    python3 -m handsfree run --source camera                  the real thing
    python3 -m handsfree run --source replay:recordings/pinch.jsonl
    python3 -m handsfree run --source synth:demo

Worth having for three reasons beyond testing: you can reproduce a bug from a
recording instead of trying to perform it in front of a lens, you can tune
thresholds against a fixed input where the only variable is `config.toml`, and
you can demonstrate the thing on a laptop with no Pi attached.

This module imports nothing hardware. `HandTracker` lives behind a lazy import
so that asking for `--source synth` on a machine with no camera, no OpenCV and
no MediaPipe works — which is most of the point.

Two details the fake sources have to get right, both of which are silently
ruinous if missed:

* **Pace yourself.** `run.py` timestamps frames with the wall clock, and every
  threshold in `config.toml` is in seconds. A source that yields as fast as it
  can compresses a twenty-second session into ten milliseconds, and every dwell,
  wake and refractory gate stops meaning anything.
* **Yield a frame, not just landmarks.** The preview draws on it. A blank
  canvas is fine; `None` is not, unless preview is off.
"""

import time

#: What `--source` accepts, for argparse and for the error message.
KINDS = ("camera", "replay", "synth")


def open(spec="camera", width=640, height=480, rotate=0, blank=True):
    """Build a frame source from a `--source` string.

    `camera` (or `camera:...`) is the real one. `replay:<path>` plays a
    recording back at its own recorded pace. `synth:<name>` generates hands.
    """
    kind, _, arg = str(spec).partition(":")
    kind = kind or "camera"

    if kind == "camera":
        # Imported here, not at module scope: this is the one branch that needs
        # picamera2, MediaPipe and OpenCV, and the others have to work without.
        from handsfree.landmarks import HandTracker
        return HandTracker(width, height, rotate=rotate)

    if kind == "replay":
        if not arg:
            raise SystemExit("--source replay needs a path, e.g. "
                             "--source replay:recordings/pinch.jsonl")
        return Recorded(arg, width, height, blank)

    if kind == "synth":
        return Generated(arg or "demo", width, height, blank)

    raise SystemExit(f"unknown --source {spec!r}; "
                     f"expected one of {', '.join(KINDS)}")


def _canvas(width, height, blank):
    """A frame to hand back. numpy if it's here, a flat buffer if it isn't."""
    if not blank:
        return None
    try:
        import numpy as np
        return np.zeros((height, width, 3), dtype=np.uint8)
    except ImportError:
        # No numpy on this machine, so preview and `draw()` aren't available
        # either. Anything not-None keeps the contract; nothing will touch it.
        return bytearray(width * height * 3)


class _Paced:
    """Common machinery: hand out frames on a clock, and stop when asked."""

    def __init__(self, width, height, blank):
        self.frame = _canvas(width, height, blank)
        self._stop = False
        self._t0 = None

    def _wait_until(self, offset):
        """Sleep until `offset` seconds after the first frame went out."""
        if self._t0 is None:
            self._t0 = time.perf_counter()
            return
        behind = (self._t0 + offset) - time.perf_counter()
        if behind > 0:
            time.sleep(behind)

    def close(self):
        self._stop = True


class Recorded(_Paced):
    """A `recordings/*.jsonl` file, played back at the pace it was recorded.

    The same landmarks the gesture layer saw when the clip was made, arriving
    with the same gaps — including the dropped frames, which are the point.
    """

    def __init__(self, path, width=640, height=480, blank=True, loop=False):
        super().__init__(width, height, blank)
        self.path = path
        self.loop = loop

    def frames(self):
        from handsfree.cli.replay import load

        while not self._stop:
            for _header, t, landmarks in load(self.path):
                if self._stop:
                    return
                self._wait_until(t)
                yield self.frame, landmarks
            if not self.loop:
                return


class Generated(_Paced):
    """Hands built by `synth.py`, following a scripted session.

    Not a recording of anything — a description of what a hand does, which
    means it can exercise gestures no clip in `recordings/` contains, and can
    be read as a specification of what should happen.
    """

    def __init__(self, script="demo", width=640, height=480, blank=True,
                 fps=9.5):
        super().__init__(width, height, blank)
        self.script = script
        self.fps = fps

    def frames(self):
        from handsfree import scripts

        for t, landmarks in scripts.build(self.script, fps=self.fps):
            if self._stop:
                return
            self._wait_until(t)
            yield self.frame, landmarks
