"""Is the vision half healthy? Measure it instead of guessing.

Open problem #1 has been "detection flickers about once a second" for as long
as the project has existed, and it was never investigated because nobody could
put a number on it. Everything downstream — every dwell, every threshold —
assumes a stable hand, so this is the one fault that makes the whole device
feel unreliable no matter how well the gesture layer is tuned.

    sudo venv/bin/python -m handsfree doctor --seconds 60

Hold one hand still in frame and it reports the four things that matter:

* **drops per minute** — how often the hand vanishes and comes back. This is
  the flicker, as a number. Zero is the goal; anything above ~2 is felt.
* **frame time** — p50 and p95, against the ~104 ms baseline from the
  recording session. Open problem #2 is a suspected 125 ms regression.
* **landmark jitter** — how much the index tip moves while the hand is still.
  Distinguishes "the tracker is unstable" from "the hand is unstable".
* **exposure and gain** — whether auto-exposure is still hunting. A camera that
  keeps re-metering changes the image from frame to frame, which is one of the
  two plausible causes of the flicker.

`--json` writes the raw per-frame log, so two runs can be compared directly
rather than by eye.
"""

import json
import statistics
import sys
import time

from handsfree.cli import parser
from handsfree.config import load_config, rotation


def summarise(frames, drops, seconds):
    """Turn the per-frame log into the numbers worth arguing about."""
    times = [f["ms"] for f in frames[1:]]
    present = [f for f in frames if f["hand"]]
    out = {
        "frames": len(frames),
        "seconds": seconds,
        "fps": len(frames) / seconds if seconds else 0,
        "hand_present": 100.0 * len(present) / len(frames) if frames else 0,
        "drops": drops,
        "drops_per_min": drops * 60.0 / seconds if seconds else 0,
    }
    if times:
        out["ms_p50"] = statistics.median(times)
        out["ms_p95"] = sorted(times)[int(len(times) * 0.95)]
        out["ms_max"] = max(times)
    tips = [(f["tip"][0], f["tip"][1]) for f in present if f.get("tip")]
    if len(tips) > 2:
        steps = [((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
                 for a, b in zip(tips, tips[1:])]
        out["jitter"] = statistics.median(steps)
        out["jitter_p95"] = sorted(steps)[int(len(steps) * 0.95)]
    lums = [f["lum"] for f in frames if f.get("lum") is not None]
    if lums:
        out["brightness"] = statistics.median(lums)
    gains = [f["gain"] for f in frames if f.get("gain")]
    exps = [f["exposure"] for f in frames if f.get("exposure")]
    if gains:
        out["gain"] = [min(gains), max(gains)]
        out["gain_moved"] = max(gains) - min(gains) > 0.01
    if exps:
        out["exposure"] = [min(exps), max(exps)]
        out["exposure_moved"] = max(exps) - min(exps) > 100
    return out


def report(s):
    print()
    print(f"  frames        {s['frames']}  over {s['seconds']:.0f}s  "
          f"({s['fps']:.1f} fps)")
    print(f"  hand present  {s['hand_present']:.1f}% of frames")

    drops = s["drops_per_min"]
    verdict = "steady" if drops < 1 else ("noticeable" if drops < 4 else "BAD")
    print(f"  drops         {s['drops']}  =  {drops:.1f}/min   <- {verdict}")
    print("                 (every threshold downstream assumes this is ~0)")

    if "ms_p50" in s:
        print(f"  frame time    p50 {s['ms_p50']:.0f}ms  p95 {s['ms_p95']:.0f}ms"
              f"  max {s['ms_max']:.0f}ms   (baseline was 104ms)")
    if "jitter" in s:
        print(f"  jitter        {s['jitter']:.4f} median, "
              f"{s['jitter_p95']:.4f} p95  (normalized units, hand held still)")
    if "brightness" in s:
        from handsfree.capture import DARK
        lum = s["brightness"]
        print(f"  brightness    {lum:.0f}/255   "
              f"<- {'fine' if lum >= DARK else 'TOO DARK - add light'}")
        if lum < DARK:
            print("                 a dark scene forces maximum gain and a long")
            print("                 exposure: noisy and motion-blurred, which")
            print("                 loses hands whatever the thresholds say")
    if "gain" in s:
        print(f"  gain          {s['gain'][0]:.2f} -> {s['gain'][1]:.2f}"
              f"   {'STILL HUNTING' if s.get('gain_moved') else 'locked'}")
    if "exposure" in s:
        print(f"  exposure      {s['exposure'][0]} -> {s['exposure'][1]}us"
              f"   {'STILL HUNTING' if s.get('exposure_moved') else 'locked'}")
    print()


def main():
    ap = parser("doctor")
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--rotate", type=int, default=None,
                    choices=[0, 90, 180, 270])
    ap.add_argument("--json", default=None,
                    help="write the per-frame log here, to compare two runs")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    from handsfree.hand import INDEX_TIP
    from handsfree.landmarks import HandTracker

    cfg = load_config()
    tracker = HandTracker(args.width, args.height, rotate=rotation(args.rotate),
                          config=cfg)

    print(f"\n  Hold one hand still in frame for {args.seconds:.0f}s.")
    print("  Don't gesture — this is measuring whether the tracker can keep "
          "hold of a hand that isn't moving.\n")

    frames = []
    drops = 0
    had_hand = False
    started = None
    last = None
    reported = 0.0

    try:
        for frame, landmarks in tracker.frames():
            now = time.perf_counter()
            if started is None:
                started = now
            elapsed = now - started
            if elapsed > args.seconds:
                break

            row = {"t": round(elapsed, 4), "hand": landmarks is not None,
                   "ms": (now - last) * 1000 if last else 0.0}
            last = now
            if landmarks:
                tip = landmarks[INDEX_TIP]
                row["tip"] = [round(tip.x, 5), round(tip.y, 5)]
            meta = tracker.metadata()
            row.update(meta)
            # Every tenth frame — mean() over 640x480 is not free at 9 fps.
            if len(frames) % 10 == 0:
                from handsfree.capture import brightness
                row["lum"] = round(brightness(frame), 1)
            frames.append(row)

            # A drop is a hand we had and then lost. Counting transitions, not
            # missing frames — one long absence is one drop, not fifty.
            if had_hand and landmarks is None:
                drops += 1
            if landmarks is not None:
                had_hand = True

            if not args.quiet and elapsed - reported >= 1.0:
                reported = elapsed
                print(f"\r  {elapsed:5.1f}s  {len(frames):4d} frames  "
                      f"{drops:3d} drops  "
                      f"{'hand' if landmarks else '  - '}   ",
                      end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        tracker.close()

    seconds = frames[-1]["t"] if frames else 0.0
    stats = summarise(frames, drops, seconds)
    report(stats)

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"stats": stats, "frames": frames}, fh)
        print(f"  per-frame log written to {args.json}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
