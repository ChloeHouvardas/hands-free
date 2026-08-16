"""Landmark geometry to gesture events.

`GestureEngine.update(landmarks, t)` is the interface everything else uses —
`replay.py` feeds it recordings, `main.py` will feed it the live camera. Keeping
that boundary means gesture logic can be tuned offline.

Right now this only knows about pinch. The full state machine (park/move/drag/
scroll/swipe) lands next, built against recorded sessions.

    python gestures.py
    python gestures.py --no-preview     # headless, events only
"""

import argparse

from hand import INDEX_TIP, THUMB_TIP, distance, hand_scale


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


class GestureEngine:
    """Turns a stream of (landmarks, timestamp) into gesture events."""

    # MediaPipe drops the odd frame at ~12fps — occlusion, motion blur. Releasing
    # a held pinch on the first missing frame fires spurious PINCH_UP mid-drag,
    # so tolerate a short gap before believing the hand is really gone.
    #
    # In seconds, not frames: the frame rate wanders between 8 and 15fps, so a
    # frame count means a different amount of real time from one run to the next.
    LOST_GRACE = 0.25

    def __init__(self):
        self.pinch = Pinch()
        self.last_seen = None
        self.lost = False

    def update(self, landmarks, t):
        """Returns a list of event strings for this frame (usually empty)."""
        events = []

        if landmarks:
            self.last_seen = t
            self.lost = False
            event = self.pinch.update(landmarks)
            if event:
                events.append(event)
        elif self.last_seen is not None and not self.lost:
            if t - self.last_seen >= self.LOST_GRACE:
                self.lost = True
                if self.pinch.down:
                    self.pinch.down = False
                    events.append("PINCH_UP")
                events.append("HAND_LOST")

        return events

    @property
    def pinching(self):
        return self.pinch.down


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--no-preview", action="store_true")
    args = ap.parse_args()

    # Imported here, not at module level, so replay.py can use this file on a
    # machine with no camera and no picamera2.
    import time

    import cv2

    from landmarks import HandTracker, draw

    tracker = HandTracker(args.width, args.height)
    engine = GestureEngine()
    t0 = time.perf_counter()

    try:
        for frame, landmarks in tracker.frames():
            for event in engine.update(landmarks, time.perf_counter() - t0):
                print(event, flush=True)

            if not args.no_preview:
                if landmarks:
                    draw(frame, landmarks)
                cv2.putText(
                    frame, "PINCH" if engine.pinching else "", (10, 30),
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
