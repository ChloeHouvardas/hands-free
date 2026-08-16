"""Step 6 — landmark geometry to gesture events. No mouse yet, just stdout.

    python gestures.py
    python gestures.py --no-preview     # headless, events only
"""

import argparse
import math

import cv2

from landmarks import HandTracker, draw, INDEX_TIP, MIDDLE_MCP, THUMB_TIP, WRIST


def distance(a, b):
    return math.hypot(a.x - b.x, a.y - b.y)


def hand_scale(landmarks):
    """Wrist to middle-finger MCP. Everything is measured relative to this so
    thresholds survive the hand moving nearer or further from the camera."""
    return distance(landmarks[WRIST], landmarks[MIDDLE_MCP])


class Pinch:
    """Thumb tip to index tip, normalized by hand size.

    Two thresholds, not one: a pinch registers below ENTER and only releases
    above EXIT. A single threshold chatters when the distance sits on it.
    """

    ENTER = 0.35
    EXIT = 0.45

    def __init__(self):
        self.down = False

    def update(self, landmarks):
        scale = hand_scale(landmarks)
        if scale == 0:
            return None
        ratio = distance(landmarks[THUMB_TIP], landmarks[INDEX_TIP]) / scale

        if not self.down and ratio < self.ENTER:
            self.down = True
            return "PINCH_DOWN"
        if self.down and ratio > self.EXIT:
            self.down = False
            return "PINCH_UP"
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--no-preview", action="store_true")
    args = ap.parse_args()

    tracker = HandTracker(args.width, args.height)
    pinch = Pinch()

    try:
        for frame, landmarks in tracker.frames():
            if landmarks:
                event = pinch.update(landmarks)
                if event:
                    print(event, flush=True)
            elif pinch.down:
                # Hand left the frame mid-pinch. Release rather than latch.
                pinch.down = False
                print("PINCH_UP (hand lost)", flush=True)

            if not args.no_preview:
                if landmarks:
                    draw(frame, landmarks)
                cv2.putText(
                    frame, "PINCH" if pinch.down else "", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2,
                )
                cv2.imshow("gestures", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    finally:
        tracker.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
