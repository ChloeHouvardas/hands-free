"""Replay a recorded session through the gesture layer.

This is the tuning loop. Change a threshold, rerun this, see what changed —
no camera, no Pi, no hand required.

    python replay.py recordings/pinch.jsonl
    python replay.py recordings/*.jsonl --quiet     # just the event counts
    python replay.py recordings/pinch.jsonl --drop 2  # simulate 1/3 the frame rate
"""

import argparse
import glob
import json
from types import SimpleNamespace

from gestures import GestureEngine


def load(path):
    """Yield (t, landmarks) pairs. landmarks is None or a list of 21 points
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


def replay(path, drop=0, quiet=False):
    engine = GestureEngine()
    events = []
    frames = hits = 0
    header = None

    for i, (header, t, landmarks) in enumerate(load(path)):
        # Simulate a lower frame rate by skipping frames.
        if drop and i % (drop + 1):
            continue
        frames += 1
        if landmarks:
            hits += 1
        for event in engine.update(landmarks, t):
            events.append((t, event))
            if not quiet:
                print(f"  {t:6.2f}s  {event}")

    label = (header or {}).get("label", path)
    rate = 100 * hits / frames if frames else 0
    print(f"{label:>10}  {frames:4d} frames  hand {rate:3.0f}%  "
          f"{len(events):3d} events")

    counts = {}
    for _, e in events:
        counts[e] = counts.get(e, 0) + 1
    if counts:
        print("            " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--drop", type=int, default=0,
                    help="skip N of every N+1 frames, to simulate a slower Pi")
    ap.add_argument("--quiet", action="store_true",
                    help="suppress per-event lines, show only the summary")
    args = ap.parse_args()

    paths = []
    for p in args.paths:
        paths.extend(sorted(glob.glob(p)) or [p])

    for path in paths:
        replay(path, drop=args.drop, quiet=args.quiet)


if __name__ == "__main__":
    main()
