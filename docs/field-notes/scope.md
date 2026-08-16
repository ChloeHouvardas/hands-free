# Prototype scope

## v1 goal

Plug the Pi into a laptop. Move your hand in front of the camera and the cursor
follows it. Pinch to click. No driver or app installed on the laptop.

## Hardware

- Raspberry Pi 4, 2 GB
- Camera Module 3 (CSI, not USB)
- Raspberry Pi OS Bookworm 64-bit (Trixie is Python 3.13; MediaPipe needs ≤3.11)

## Phases

### 1. Detect gestures accurately

- MediaPipe **Hand Landmarker** — pretrained, 21 landmarks, no training needed
- Not Gesture Recognizer — its canned gestures aren't ours
- Gestures are geometry over landmarks, not ML:
  - Pinch = distance(landmark 4, landmark 8), normalized by hand size
  - Cursor = wrist delta
  - Swipe = extended finger count + direction over a short window
- Smoothing via 1€ filter; hysteresis on the pinch threshold so it doesn't chatter
- An engage/disengage state, so the cursor isn't live every time a hand appears
- Done when: gestures fire reliably, and don't fire when they shouldn't

Setup — two known reasons the official
[Pi sample](https://github.com/google-ai-edge/mediapipe-samples/tree/main/examples/gesture_recognizer/raspberry_pi)
fails, stacked:

- **Pin `mediapipe==0.10.18`.** Last 0.10.x with an aarch64 Linux wheel —
  0.10.20 through 0.10.35 are x86_64 only. aarch64 returns at 1.0.x, but the
  samples target the 0.10 API
- **Capture with `picamera2`, not `cv2.VideoCapture`** — the sample assumes a USB
  webcam; Camera Module 3 is CSI/libcamera and OpenCV can't see it
- venv needs `--system-site-packages` (picamera2 is apt, MediaPipe is pip)
- Install mediapipe first, then `opencv-python==4.11.0.86` (numpy pin conflict)

### 2. Optimize inference on the Pi

- Baseline first: sustained FPS, latency, CPU at 640×480 and 320×240,
  `num_hands=1`. Optimize against a number, not a feeling
- Cheap wins in order: lower input resolution, one hand only, detect-then-track
  instead of per-frame detection
- Then ncnn, if the profile says the model is the bottleneck. Convert MediaPipe's
  `.tflite` (a `.task` file is a zip) via [PNNX](https://github.com/pnnx/pnnx) —
  no training required. Cost is rebuilding what ncnn doesn't ship: palm-detect →
  ROI crop → landmark → track. Reference point: ncnn hand pose runs
  [7 FPS on a bare Pi 4](https://github.com/Qengineering/Hand-Pose-ncnn-Raspberry-Pi-4)
- Coral USB accelerator is the Pi 4 hardware option (Hailo needs a Pi 5 M.2)
- Done when: latency is low enough that the cursor feels attached to the hand

### 3. Control the laptop

- Pi 4 as a USB HID gadget: `dtoverlay=dwc2,dr_mode=peripheral`,
  `modules-load=dwc2,g_hid`, build the gadget with ConfigFS, write reports to
  `/dev/hidg0`
- Relative mouse deltas — the standard descriptor, same behaviour on every host.
  Absolute needs a digitizer descriptor and is less portable
- Runs over the USB-C port, which is also the power port — the host has to supply
  enough current to run the Pi
- Done when: it works on a laptop that has never seen the device before
