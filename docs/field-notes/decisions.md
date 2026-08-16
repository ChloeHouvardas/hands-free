# Decisions

| Feature | Tech | Why |
| --- | --- | --- |
| Real-time hand tracking | MediaPipe Hand Landmarker | Pretrained 21-landmark model plus the detect → crop → track pipeline around it. |
| Gesture classification | Landmark geometry | Pinch is a distance between two points; no model, no dataset, tunable by editing a constant. |
| Camera capture | picamera2 | Camera Module 3 is a CSI camera on the libcamera stack. |
| MediaPipe version | Pinned `0.10.35` | The version the Pi samples target. |
| Where inference runs | On the Pi | Keeps the host driverless and OS-agnostic. |
