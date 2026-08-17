"""Step 3 — camera only, no ML.

Proves the picamera2 path works on its own, so that when MediaPipe misbehaves
later it's unambiguous which half is at fault.

    python capture.py
    python capture.py --width 320 --height 240
"""

import argparse
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    args = ap.parse_args()

    picam2 = open_camera(args.width, args.height)
    last, fps = time.perf_counter(), 0.0

    try:
        while True:
            frame = picam2.capture_array()

            now = time.perf_counter()
            fps = 0.9 * fps + 0.1 * (1.0 / (now - last))
            last = now

            cv2.putText(
                frame, f"{fps:5.1f} fps", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2,
            )
            cv2.imshow("capture", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        picam2.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
