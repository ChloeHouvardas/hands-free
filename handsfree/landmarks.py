"""21 hand landmarks from the camera.

`HandTracker` is the shared camera+MediaPipe object used by bench.py,
gestures.py and record.py.

    python3 -m handsfree landmarks

Needs hand_landmarker.task in the repo root:
    wget -O hand_landmarker.task \\
      https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task
"""

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

from handsfree.capture import open_camera
from handsfree.config import ROOT, rotation
# Constants and geometry live in hand.py, which imports nothing — so the gesture
# layer stays usable on a machine without a camera.
from handsfree.hand import CONNECTIONS, INDEX_TIP, MIDDLE_MCP, THUMB_TIP, WRIST  # noqa: F401

# Anchored to the repo root, not the working directory — otherwise this only
# resolves when you happen to have cd'd into the repo first.
MODEL = os.path.join(ROOT, "hand_landmarker.task")


class HandTracker:
    """Camera + MediaPipe. Yields (frame, landmarks) where landmarks is a list
    of 21 normalized landmarks, or None when no hand is visible."""

    # cv2 rotation codes. MediaPipe is trained on upright hands and gets
    # noticeably worse on rotated ones, so if the camera is physically mounted
    # sideways, straighten the frame before it ever reaches the model.
    ROTATIONS = {
        90: cv2.ROTATE_90_CLOCKWISE,
        180: cv2.ROTATE_180,
        270: cv2.ROTATE_90_COUNTERCLOCKWISE,
    }

    def __init__(self, width=640, height=480, num_hands=1, model=MODEL,
                 rotate=0):
        if rotate not in (0, 90, 180, 270):
            raise ValueError("rotate must be 0, 90, 180 or 270")
        self.rotate = self.ROTATIONS.get(rotate)
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
            if self.rotate is not None:
                frame = cv2.rotate(frame, self.rotate)
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
