"""Replay a recorded session through the gesture layer.

This is the tuning loop, and it's the reason Phase A existed. Change a
threshold, rerun this, see exactly what moved — no camera, no Pi, no hand.

    python replay.py recordings/pinch.jsonl
    python replay.py recordings/*.jsonl --check    # pass/fail against EXPECT
    python replay.py recordings/*.jsonl --drop 2   # simulate a slower Pi

`--check` is the one that matters. Each clip was recorded doing a known thing,
so we know roughly what should come out and — just as importantly — what
shouldn't. `nothing.jsonl` is the control: a hand pottering about that must
produce no clicks, no scrolls and no swipes.
"""

import argparse
import glob
import json
import os
from types import SimpleNamespace

from handsfree.gestures import GestureEngine

# clip -> {event: (min, max)}. None as a bound means don't care.
#
# Ranges, not exact counts, because these are recordings of a real hand being
# realistically sloppy. The bounds come from reading the raw signals rather
# than from the instructions given at record time — the pinch clip was meant
# to have ten pinches and actually contains five, and the drag clip is very
# nearly one continuous held pinch because the thumb never fully lets go.
EXPECT = {
    # Controls. These run from cold, as an idle hand really would.
    "baseline": {"click_down": (0, 0), "scroll": (0, 0), "swipe": (0, 0)},
    "park":     {"click_down": (0, 0), "scroll": (0, 0), "swipe": (0, 0)},
    "nothing":  {"click_down": (0, 1), "scroll": (0, 0), "swipe": (0, 0)},

    # Gestures. Primed, because each was recorded already mid-gesture.
    "move":     {"cursor": (50, None), "click_down": (0, 1), "scroll": (0, 0),
                 "swipe": (0, 0)},
    "pinch":    {"click_down": (5, 6), "click_up": (4, 6)},
    "drag":     {"click_down": (2, 5), "click_up": (1, 5)},
    "scroll":   {"scroll": (20, None), "click_down": (0, 1), "swipe": (0, 0)},
    "swipe":    {"swipe": (5, 9), "click_down": (0, 1)},
    "lost":     {"hand_lost": (2, None), "click_down": (2, 8)},
}

# Clips recorded mid-gesture, with no pointing lead-in to wake the machine.
PRIMED = {"move", "pinch", "drag", "scroll", "swipe", "lost"}


def load(path):
    """Yield (header, t, landmarks). landmarks is None or a list of 21 points
    with .x/.y/.z, matching what HandTracker hands the gesture layer."""
    header = None
    with open(path) as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("header"):
                header = rec
                continue
            lm = rec["lm"]
            pts = None if lm is None else [
                SimpleNamespace(x=p[0], y=p[1], z=p[2]) for p in lm
            ]
            yield header, rec["t"], pts


def replay(path, drop=0, verbose=False):
    engine = GestureEngine()
    counts = {}
    states = {}
    frames = hits = 0
    header = None
    label = os.path.basename(path)[:-6]
    primed = label in PRIMED

    for i, (header, t, landmarks) in enumerate(load(path)):
        # Simulate a lower frame rate by skipping frames. Everything here is
        # time-gated rather than frame-gated, so the results should barely
        # move — and if they do, that's a bug worth knowing about.
        if drop and i % (drop + 1):
            continue
        frames += 1
        if landmarks:
            hits += 1
        for event in engine.update(landmarks, t):
            counts[event.name] = counts.get(event.name, 0) + 1
            if verbose and event.name != "cursor":
                print(f"  {t:6.2f}s  {event}")
        states[engine.state] = states.get(engine.state, 0) + 1

        # A primed clip is one where we pretend the user has already pointed.
        # None of them contain a deliberate park, and several have the hand
        # leave the frame partway, so control is re-taken whenever the engine
        # falls back to PARKED rather than only at the start.
        if primed and landmarks and engine.state == "PARKED":
            engine.take_control(landmarks, t)

    return label, frames, hits, counts, states


def check(label, counts):
    """Compare against EXPECT. Returns a list of complaints."""
    bad = []
    for event, (lo, hi) in EXPECT.get(label, {}).items():
        n = counts.get(event, 0)
        if lo is not None and n < lo:
            bad.append(f"{event} {n} < {lo}")
        if hi is not None and n > hi:
            bad.append(f"{event} {n} > {hi}")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--drop", type=int, default=0,
                    help="skip N of every N+1 frames, to simulate a slower Pi")
    ap.add_argument("--check", action="store_true",
                    help="pass/fail each clip against EXPECT")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="print every event as it fires")
    args = ap.parse_args()

    paths = []
    for p in args.paths:
        paths.extend(sorted(glob.glob(p)) or [p])

    failures = 0
    for path in paths:
        label, frames, hits, counts, states = replay(path, args.drop, args.verbose)
        rate = 100 * hits / frames if frames else 0
        summary = "  ".join(f"{k}={v}" for k, v in sorted(counts.items())
                            if k != "cursor")
        top = max(states, key=states.get) if states else "-"

        line = (f"{label:>9}  {frames:4d}f  hand {rate:3.0f}%  "
                f"mostly {top:<7}  {summary}")

        if args.check:
            bad = check(label, counts)
            failures += bool(bad)
            print(("FAIL " if bad else "pass ") + line)
            if bad:
                print(f"{'':>11}-> " + ", ".join(bad))
        else:
            print(line)

    if args.check:
        print(f"\n{len(paths) - failures}/{len(paths)} clips pass")
        raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
