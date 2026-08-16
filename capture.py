"""Step 3 — camera only, no ML.

Proves the picamera2 path works on its own, so that when MediaPipe misbehaves
later it's unambiguous which half is at fault.

    python capture.py
    python capture.py --width 320 --height 240
"""

import argparse
import time

import cv2
from picamera2 import Picamera2


def open_camera(width, height):
    picam2 = Picamera2()
    # Despite the name, picamera2's "RGB888" hands back arrays in BGR order,
    # which is what OpenCV wants. Converting for MediaPipe happens downstream.
    picam2.configure(
        picam2.create_preview_configuration(
            main={"size": (width, height), "format": "RGB888"}
        )
    )
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
