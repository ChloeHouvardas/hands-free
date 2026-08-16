"""Step 5 — the Phase 1 deliverable: numbers.

Run this DISCONNECTED from Pi Connect screen share. The desktop session and its
video encoding are real CPU on a 2GB Pi and will corrupt the results.

    python bench.py                    # 640x480 and 320x240
    python bench.py --seconds 60

Reports FPS, per-frame latency, CPU, and landmark jitter. Jitter is measured on
a deliberately still hand and is the number that decides whether cursor control
feels usable — hold your hand steady in frame for the whole run.
"""

import argparse
import statistics
import time

import numpy as np
import psutil

from landmarks import HandTracker, INDEX_TIP

RESOLUTIONS = [(640, 480), (320, 240)]


def run(width, height, seconds, num_hands):
    tracker = HandTracker(width, height, num_hands=num_hands)
    proc = psutil.Process()
    proc.cpu_percent()  # first call primes the counter

    latencies, tip_positions = [], []
    frames = 0
    deadline = time.perf_counter() + seconds
    start = time.perf_counter()

    try:
        for frame, landmarks in tracker.frames():
            t = time.perf_counter()
            if frames:  # skip the first frame, it includes warm-up
                latencies.append((t - last) * 1000)
            last = t
            frames += 1

            if landmarks:
                tip = landmarks[INDEX_TIP]
                tip_positions.append((tip.x * width, tip.y * height))

            if t > deadline:
                break
    finally:
        cpu = proc.cpu_percent()
        tracker.close()

    elapsed = time.perf_counter() - start
    return {
        "res": f"{width}x{height}",
        "fps": frames / elapsed,
        "p50": statistics.median(latencies) if latencies else float("nan"),
        "p95": (np.percentile(latencies, 95) if latencies else float("nan")),
        "cpu": cpu,
        "detected": f"{len(tip_positions)}/{frames}",
        "jitter": jitter(tip_positions),
    }


def jitter(positions):
    """Mean frame-to-frame movement of the index tip, in pixels. On a still
    hand this is noise, not motion."""
    if len(positions) < 2:
        return float("nan")
    p = np.array(positions)
    return float(np.mean(np.linalg.norm(np.diff(p, axis=0), axis=1)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=30)
    ap.add_argument("--hands", type=int, default=1)
    args = ap.parse_args()

    print("Hold one hand still in frame for the whole run.\n")
    rows = []
    for w, h in RESOLUTIONS:
        print(f"running {w}x{h} for {args.seconds}s ...")
        rows.append(run(w, h, args.seconds, args.hands))

    print(f"\n{'res':>10} {'fps':>7} {'p50 ms':>8} {'p95 ms':>8} "
          f"{'cpu %':>7} {'detected':>10} {'jitter px':>10}")
    for r in rows:
        print(f"{r['res']:>10} {r['fps']:7.1f} {r['p50']:8.1f} {r['p95']:8.1f} "
              f"{r['cpu']:7.1f} {r['detected']:>10} {r['jitter']:10.2f}")


if __name__ == "__main__":
    main()
