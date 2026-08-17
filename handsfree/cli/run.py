"""The live app: camera -> landmarks -> gestures -> stdout.

    python gestures.py
    python gestures.py --rotate 90
"""

import argparse

from handsfree.config import rotation
from handsfree.gestures import GestureEngine


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--rotate", type=int, default=None, choices=[0, 90, 180, 270],
                    help="override the mounting angle in config.toml")
    ap.add_argument("--no-preview", action="store_true")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()

    # Imported after parsing, not at module level, so `--help` still works on a
    # machine with no camera and no MediaPipe — which is how you check the flags
    # from a laptop before running it on the Pi.
    import time

    import cv2

    from handsfree.landmarks import HandTracker, draw
    from handsfree.preview import Preview

    tracker = HandTracker(args.width, args.height,
                          rotate=rotation(args.rotate))
    preview = None if args.no_preview else Preview(port=args.port)
    engine = GestureEngine()
    t0 = time.perf_counter()

    if preview:
        print(preview.banner(), flush=True)
    print("  Point at the camera to take control. Open palm parks it.\n",
          flush=True)

    try:
        for frame, landmarks in tracker.frames():
            t = time.perf_counter() - t0
            for event in engine.update(landmarks, t):
                if event.name != "cursor":
                    print(f"{t:6.2f}s  {event}", flush=True)

            if preview:
                if landmarks:
                    draw(frame, landmarks)
                h, w = frame.shape[:2]
                cv2.putText(frame, engine.state, (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 220, 255), 2)
                cv2.putText(frame, engine.pose.current, (10, 60),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
                cx = int(engine.cursor[0] * w)
                cy = int(engine.cursor[1] * h)
                cv2.circle(frame, (cx, cy), 9, (255, 0, 255), 2)
                preview.update(frame)
    finally:
        tracker.close()
        if preview:
            preview.close()


if __name__ == "__main__":
    main()
