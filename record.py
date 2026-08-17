"""Record landmark sessions to JSONL so gestures can be tuned offline.

The whole point: tuning thresholds shouldn't need a human, a camera and a hand
every time. Record once, replay forever.

Guided session (what you want the first time):

    python record.py --session

Then open http://<pi>:8080 on your laptop to see what the camera sees.

Single clip:

    python record.py --label pinch --seconds 15

Output goes to recordings/<label>.jsonl. First line is a header; every line
after is one frame.
"""

import argparse
import json
import os
import sys
import threading
import time

import cv2

from landmarks import HandTracker, draw
from config import rotation
from preview import Preview, lan_ip

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

GREEN, RED, WHITE, YELLOW = (0, 220, 0), (0, 0, 255), (255, 255, 255), (0, 200, 255)


class Recorder:
    """Runs the camera continuously on a background thread.

    Continuous rather than only-while-recording so the live view stays up during
    the prompts — which is the whole point of having a live view: you can frame
    your hand *before* the countdown ends.
    """

    def __init__(self, tracker, preview=None):
        self.tracker = tracker
        self.preview = preview
        self.lock = threading.Lock()

        self.mode = "idle"          # idle | countdown | recording | done
        self.label = ""
        self.prompt_n = ""
        self.count = 0              # countdown number to show
        self.seconds = 0
        self.file = None
        self.t0 = 0.0
        self.frames = 0
        self.hits = 0
        self.hand = False
        self.fps = 0.0
        self._stop = False

        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    # -- background thread ------------------------------------------------

    def _loop(self):
        last = time.perf_counter()
        for frame, landmarks in self.tracker.frames():
            if self._stop:
                break

            now = time.perf_counter()
            dt = now - last
            last = now
            if dt > 0:
                self.fps = 0.9 * self.fps + 0.1 * (1 / dt)
            self.hand = landmarks is not None

            with self.lock:
                if self.mode == "recording":
                    t = now - self.t0
                    if t >= self.seconds:
                        self._close_file()
                    else:
                        pts = None if landmarks is None else [
                            [round(p.x, 5), round(p.y, 5), round(p.z, 5)]
                            for p in landmarks
                        ]
                        self.file.write(
                            json.dumps({"t": round(t, 4), "lm": pts}) + "\n")
                        self.frames += 1
                        if landmarks:
                            self.hits += 1

            if self.preview:
                if landmarks:
                    draw(frame, landmarks)
                self._annotate(frame)
                self.preview.update(frame)

    def _annotate(self, frame):
        h, w = frame.shape[:2]

        if self.hand:
            cv2.putText(frame, "HAND OK", (10, h - 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, GREEN, 2)
        else:
            cv2.putText(frame, "NO HAND", (10, h - 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, RED, 2)

        cv2.putText(frame, f"{self.fps:.0f} fps", (w - 95, h - 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, WHITE, 1)

        if self.prompt_n:
            cv2.putText(frame, self.prompt_n, (10, 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, YELLOW, 2)

        if self.mode == "countdown":
            text = str(self.count) if self.count else "GO"
            size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 4, 8)[0]
            cv2.putText(frame, text, ((w - size[0]) // 2, (h + size[1]) // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 4, YELLOW, 8)

        elif self.mode == "recording":
            left = max(0.0, self.seconds - (time.perf_counter() - self.t0))
            cv2.circle(frame, (w - 25, 25), 10, RED, -1)
            cv2.putText(frame, f"REC {left:4.1f}s", (w - 145, 32),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, RED, 2)
            # progress bar along the bottom
            done = int(w * (1 - left / self.seconds))
            cv2.rectangle(frame, (0, h - 5), (done, h), RED, -1)

    # -- main thread ------------------------------------------------------

    def set_prompt(self, text, count=0, mode="idle"):
        with self.lock:
            self.prompt_n = text
            self.count = count
            self.mode = mode

    def start(self, label, seconds):
        os.makedirs(OUT_DIR, exist_ok=True)
        path = os.path.join(OUT_DIR, f"{label}.jsonl")
        f = open(path, "w")
        f.write(json.dumps({
            "header": True, "label": label, "seconds": seconds,
            "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }) + "\n")
        with self.lock:
            self.file, self.label, self.seconds = f, label, seconds
            self.frames = self.hits = 0
            self.t0 = time.perf_counter()
            self.mode = "recording"

    def _close_file(self):
        """Called with the lock held."""
        if self.file:
            self.file.close()
            self.file = None
        self.mode = "done"

    @property
    def recording(self):
        with self.lock:
            return self.mode == "recording"

    def close(self):
        self._stop = True
        self.thread.join(timeout=3)
        with self.lock:
            self._close_file()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", action="store_true",
                    help="guided run through every clip we need")
    ap.add_argument("--label", help="record a single clip with this name")
    ap.add_argument("--seconds", type=int, default=20)
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--rotate", type=int, default=None,
                    choices=[0, 90, 180, 270],
                    help="override the mounting angle in config.toml")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--no-preview", action="store_true")
    args = ap.parse_args()

    if not args.session and not args.label:
        ap.error("pass --session or --label")

    clips = SESSION if args.session else [(args.label, args.seconds, None)]

    print("\nStarting the camera (a few seconds)...")
    tracker = HandTracker(args.width, args.height, rotate=rotation(args.rotate))
    preview = None if args.no_preview else Preview(port=args.port)
    rec = Recorder(tracker, preview)

    total = sum(s for _, s, _ in clips)
    print("\n" + "=" * 64)
    print("  Ignore the MediaPipe warnings above — they're normal.")
    if preview:
        print(f"\n  >>> OPEN THIS ON YOUR LAPTOP:  http://{lan_ip()}:{args.port}")
        print("      (or http://chloespie.local:%d)\n" % args.port)
        print("  You'll see the camera with landmarks drawn on your hand,")
        print("  a countdown, and a red REC bar while it's capturing.")
    print(f"\n  {len(clips)} clips, {total}s of recording.")
    print("=" * 64 + "\n")

    if preview:
        print("Waiting for you to open that page", end="", flush=True)
        for _ in range(60):
            if preview.watching:
                break
            print(".", end="", flush=True)
            time.sleep(1)
        print(" connected!\n" if preview.watching
              else "\n  (carrying on without it)\n")

    try:
        for i, (label, seconds, prompt) in enumerate(clips, 1):
            header = f"{i}/{len(clips)}  {label}"
            print(f"--- {header} ({seconds}s) ---")
            if prompt:
                print(f"     {prompt}")

            rec.set_prompt(header)
            if sys.stdin.isatty():
                input("\n  Get into position, then press ENTER. ")
                for n in (3, 2, 1):
                    rec.set_prompt(header, count=n, mode="countdown")
                    print(f"\r  starting in {n}...  ", end="", flush=True)
                    time.sleep(1)
                rec.set_prompt(header, count=0, mode="countdown")
                print("\r  GO                 ")

            rec.start(label, seconds)
            while rec.recording:
                with rec.lock:
                    left = seconds - (time.perf_counter() - rec.t0)
                    n, hand = rec.frames, rec.hand
                print(f"\r  {left:5.1f}s left   hand: {'YES' if hand else 'no '}"
                      f"   {n:4d} frames   ", end="", flush=True)
                time.sleep(0.1)
            print()

            frames, hits = rec.frames, rec.hits
            rate = 100 * hits / frames if frames else 0
            flag = "" if rate > 80 else "   <-- LOW, consider re-recording"
            print(f"  saved {frames} frames, {frames / seconds:.1f} fps, "
                  f"hand in {rate:.0f}%{flag}\n")
            rec.set_prompt("")
    finally:
        rec.close()
        tracker.close()
        if preview:
            preview.close()

    print(f"Done. Recordings are in {OUT_DIR}/")


if __name__ == "__main__":
    main()
