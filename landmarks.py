"""21 hand landmarks from the camera.

`HandTracker` is the shared camera+MediaPipe object used by bench.py,
gestures.py and record.py.

    python landmarks.py

Needs hand_landmarker.task in the repo root:
    wget -O hand_landmarker.task \\
      https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task
"""

import argparse
import os
import time

# MediaPipe logs a wall of warnings on import, which buries record.py's prompts.
# Must be set before the import below, not after.
os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

from capture import open_camera
# Constants and geometry live in hand.py, which imports nothing — so the gesture
# layer stays usable on a machine without a camera.
from hand import CONNECTIONS, INDEX_TIP, MIDDLE_MCP, THUMB_TIP, WRIST  # noqa: F401

MODEL = "hand_landmarker.task"


class HandTracker:
    """Camera + MediaPipe. Yields (frame, landmarks) where landmarks is a list
    of 21 normalized landmarks, or None when no hand is visible."""

    def __init__(self, width=640, height=480, num_hands=1, model=MODEL):
        self.picam2 = open_camera(width, height)
        self.detector = vision.HandLandmarker.create_from_options(
            vision.HandLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=model),
                running_mode=vision.RunningMode.VIDEO,
                num_hands=num_hands,
            )
        )
        self._t0 = time.perf_counter()

    def frames(self):
        while True:
            frame = self.picam2.capture_array()
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

            # detect_for_video needs a monotonically increasing timestamp.
            ts = int((time.perf_counter() - self._t0) * 1000)
            result = self.detector.detect_for_video(image, ts)

            hands = result.hand_landmarks
            yield frame, (hands[0] if hands else None)

    def close(self):
        # stop() alone leaves the camera claimed, so a second HandTracker in the
        # same process (bench.py runs two) fails to open it.
        self.picam2.stop()
        self.picam2.close()


def draw(frame, landmarks):
    h, w = frame.shape[:2]
    pts = [(int(lm.x * w), int(lm.y * h)) for lm in landmarks]
    for a, b in CONNECTIONS:
        cv2.line(frame, pts[a], pts[b], (0, 255, 0), 2)
    for x, y in pts:
        cv2.circle(frame, (x, y), 4, (0, 0, 255), -1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    args = ap.parse_args()

    tracker = HandTracker(args.width, args.height)
    last, fps = time.perf_counter(), 0.0

    try:
        for frame, landmarks in tracker.frames():
            if landmarks:
                draw(frame, landmarks)

            now = time.perf_counter()
            fps = 0.9 * fps + 0.1 * (1.0 / (now - last))
            last = now
            cv2.putText(
                frame, f"{fps:5.1f} fps", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2,
            )

            cv2.imshow("landmarks", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        tracker.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
