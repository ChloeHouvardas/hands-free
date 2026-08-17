"""The live app: camera -> landmarks -> gestures -> the Mac.

    sudo python3 -m handsfree run
    sudo python3 -m handsfree run --transport null      no Mac, just print
    python3 -m handsfree run --rotate 90

Needs root, because the Bluetooth transport listens on privileged L2CAP ports.
`--transport null` doesn't, and is the way to watch the gesture layer work
without anything touching the Mac.
"""


from handsfree.cli import parser
from handsfree.config import load_config, rotation
from handsfree.gestures import GestureEngine
from handsfree.transport import BACKENDS


def main():
    ap = parser("run")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--rotate", type=int, default=None, choices=[0, 90, 180, 270],
                    help="override the mounting angle in config.toml")
    ap.add_argument("--transport", choices=BACKENDS, default=None,
                    help="override [hid] backend in config.toml")
    ap.add_argument("--pointer", choices=("relative", "absolute"), default=None,
                    help="override [hid] pointer in config.toml")
    ap.add_argument("--source", default="camera",
                    help="camera | replay:<path> | synth:<script> — where "
                         "frames come from. The fakes need no camera at all.")
    ap.add_argument("--no-preview", action="store_true")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()

    # Imported after parsing, not at module level, so `--help` still works on a
    # machine with no camera and no MediaPipe — which is how you check the flags
    # from a laptop before running it on the Pi.
    import time

    from handsfree import sources
    from handsfree import transport as transports
    from handsfree.driver import Driver
    from handsfree.preview import Preview

    cfg = load_config()
    hid_cfg = dict(cfg.get("hid", {}))
    if args.pointer:
        hid_cfg["pointer"] = args.pointer
    backend = args.transport or hid_cfg.get("backend", "null")

    # `sources.open` pulls in the camera stack only for --source camera, so a
    # replay or synth run works on a laptop with no picamera2 and no MediaPipe.
    tracker = sources.open(args.source, args.width, args.height,
                           rotate=rotation(args.rotate))
    preview = None if args.no_preview else Preview(port=args.port)

    # cv2 and draw() are only needed to annotate frames for the preview, and
    # the preview is the one part a fake source can't usefully feed.
    cv2 = draw = None
    if preview:
        import cv2
        from handsfree.landmarks import draw
    engine = GestureEngine(cfg)
    wire = transports.open(backend, hid_cfg)
    driver = Driver(wire, hid_cfg)
    # Release the button before the process goes away, however it goes away.
    driver.install_signal_guards()
    t0 = time.perf_counter()

    if preview:
        print(preview.banner(), flush=True)
    print(f"  transport: {backend} ({hid_cfg.get('pointer', 'relative')})"
          + (f"   source: {args.source}" if args.source != "camera" else ""),
          flush=True)
    print("  Point at the camera to take control. Open palm parks it.\n",
          flush=True)

    try:
        for frame, landmarks in tracker.frames():
            t = time.perf_counter() - t0
            events = engine.update(landmarks, t)
            driver.handle(events)
            for event in events:
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
                if not wire.connected:
                    cv2.putText(frame, "no host", (10, 90),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                cx = int(engine.cursor[0] * w)
                cy = int(engine.cursor[1] * h)
                cv2.circle(frame, (cx, cy), 9, (255, 0, 255), 2)
                preview.update(frame)
    finally:
        # driver.close() releases the button and closes the transport under it.
        driver.close()
        tracker.close()
        if preview:
            preview.close()


if __name__ == "__main__":
    main()
