"""Step 3 — camera only, no ML.

Proves the picamera2 path works on its own, so that when MediaPipe misbehaves
later it's unambiguous which half is at fault.

    python3 -m handsfree capture
    python3 -m handsfree capture --width 320 --height 240
"""

import os
import time

# Both libraries are extremely chatty on import, which buries the prompts in
# record.py. Set before importing them or it has no effect.
os.environ.setdefault("LIBCAMERA_LOG_LEVELS", "*:ERROR")
os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

import cv2
from picamera2 import Picamera2


def open_camera(width, height, fps=30):
    try:
        picam2 = Picamera2()
    except RuntimeError as e:
        # Almost always a previous run that didn't exit. The raw error talks
        # about acquisition sequences and tells you nothing useful.
        raise SystemExit(
            f"\nCan't open the camera: {e}\n"
            f"Usually an earlier run is still holding it. Check with:\n"
            f"    pgrep -af 'python.*(gestures|record|landmarks|capture)'\n"
            f"and stop it before starting another.\n"
        ) from None
    # Despite the name, picamera2's "RGB888" hands back arrays in BGR order,
    # which is what OpenCV wants. Converting for MediaPipe happens downstream.
    config = picam2.create_preview_configuration(
        main={"size": (width, height), "format": "RGB888"}
    )
    # Without an explicit limit the camera settles around 19fps. It makes no
    # difference to throughput (MediaPipe is the bottleneck at ~125ms/frame),
    # but it does cap how fresh a frame can be when we ask for one.
    frame_us = int(1e6 / fps)
    config["controls"] = {"FrameDurationLimits": (frame_us, frame_us)}
    picam2.configure(config)
    picam2.start()
    time.sleep(1)  # let auto-exposure settle
    return picam2
