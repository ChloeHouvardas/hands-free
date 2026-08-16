"""Record landmark sessions to JSONL so gestures can be tuned offline.

The whole point: tuning thresholds shouldn't need a human, a camera and a hand
every time. Record once, replay forever.

Guided session (what you want the first time):

    python record.py --session

Single clip:

    python record.py --label pinch --seconds 15

Output goes to recordings/<label>.jsonl. First line is a header with metadata;
every line after is one frame.
"""

import argparse
import json
import os
import sys
import time

from landmarks import HandTracker

OUT_DIR = "recordings"

# label, seconds, what to tell the person holding the hand
SESSION = [
    ("baseline", 40,
     "Hold ONE HAND STILL in frame, fingers relaxed and visible.\n"
     "     This is the FPS measurement and the jitter baseline — try not to move."),
    ("park", 15,
     "OPEN PALM, all five fingers spread, held still.\n"
     "     This is the 'parked' pose — the cursor will be frozen in this state."),
    ("move", 20,
     "POINT with your index finger and move your hand around the frame.\n"
     "     Cover the corners. This is normal cursor movement."),
    ("pinch", 20,
     "PINCH thumb and index together, then release. Repeat slowly, ~10 times.\n"
     "     Leave a clear gap between pinches."),
    ("drag", 20,
     "PINCH, HOLD, move your hand somewhere, then RELEASE. Repeat ~5 times.\n"
     "     This is click-and-drag."),
    ("scroll", 20,
     "TWO FINGERS (index + middle) extended. Move your hand up and down.\n"
     "     Pause in the middle between direction changes."),
    ("swipe", 20,
     "THREE FINGERS extended. Swipe left, pause, swipe right. Repeat ~6 times.\n"
     "     Make them deliberate and large — small flicks won't register."),
    ("nothing", 30,
     "Hand in frame doing NOTHING gesture-like — scratch your head, rest it,\n"
     "     reach past the camera, type. This is the false-positive control:\n"
     "     nothing here should ever fire a gesture."),
    ("lost", 20,
     "PINCH and HOLD, then move your hand OUT OF FRAME while still pinched.\n"
     "     Come back, repeat ~5 times. Tests that we don't latch a stuck button."),
]


def record(tracker, label, seconds):
    """Record one clip. Returns (frames, frames_with_hand)."""
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{label}.jsonl")

    frames = hits = 0
    t0 = time.perf_counter()

    with open(path, "w") as f:
        f.write(json.dumps({
            "header": True,
            "label": label,
            "seconds": seconds,
            "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }) + "\n")

        for _, landmarks in tracker.frames():
            t = time.perf_counter() - t0
            if t > seconds:
                break

            if landmarks:
                pts = [[round(p.x, 5), round(p.y, 5), round(p.z, 5)]
                       for p in landmarks]
                hits += 1
            else:
                pts = None

            f.write(json.dumps({"t": round(t, 4), "lm": pts}) + "\n")
            frames += 1

            left = seconds - t
            print(f"\r  {label}  {left:4.1f}s left   "
                  f"hand: {'YES' if landmarks else 'no '}  "
                  f"{frames / max(t, 1e-6):4.1f} fps   ", end="", flush=True)

    print()
    return frames, hits


def summarise(label, frames, hits, seconds):
    rate = 100 * hits / frames if frames else 0
    fps = frames / seconds
    flag = "" if rate > 80 else "   <-- LOW, consider re-recording"
    print(f"  saved {frames} frames, {fps:.1f} fps, hand in {rate:.0f}%{flag}\n")


def countdown(n=3):
    for i in range(n, 0, -1):
        print(f"\r  starting in {i}...  ", end="", flush=True)
        time.sleep(1)
    print("\r  GO                 ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", action="store_true",
                    help="guided run through every clip we need")
    ap.add_argument("--label", help="record a single clip with this name")
    ap.add_argument("--seconds", type=int, default=20)
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    args = ap.parse_args()

    if not args.session and not args.label:
        ap.error("pass --session or --label")

    clips = SESSION if args.session else [(args.label, args.seconds, None)]

    print("\nStarting the camera (a few seconds)...\n")
    tracker = HandTracker(args.width, args.height)

    try:
        for i, (label, seconds, prompt) in enumerate(clips, 1):
            print(f"--- {i}/{len(clips)}: {label} ({seconds}s) ---")
            if prompt:
                print(f"     {prompt}")
            if sys.stdin.isatty():
                input("\n  Get into position, then press ENTER. ")
                countdown()
            frames, hits = record(tracker, label, seconds)
            summarise(label, frames, hits, seconds)
    finally:
        tracker.close()

    print(f"Done. Recordings are in {OUT_DIR}/")


if __name__ == "__main__":
    main()
