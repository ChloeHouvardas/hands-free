"""Draw the 21 landmarks on the live camera view.

    python3 -m handsfree landmarks
"""

import time

import cv2

from handsfree.cli import parser
from handsfree.config import rotation
from handsfree.landmarks import HandTracker, draw


def main():
    ap = parser("landmarks")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--rotate", type=int, default=None,
                    choices=[0, 90, 180, 270],
                    help="override the mounting angle in config.toml")
    args = ap.parse_args()

    tracker = HandTracker(args.width, args.height, rotate=rotation(args.rotate))
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
