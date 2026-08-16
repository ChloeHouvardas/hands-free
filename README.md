# hands-free

A plug-in device that watches your hand and acts as a mouse. A Raspberry Pi with
a camera runs hand tracking locally and speaks to the host as a USB HID mouse —
no driver, no app.

Early prototype.

- [field-notes/architecture.md](field-notes/architecture.md) — the system, and the order we build it in
- [field-notes/scope.md](field-notes/scope.md) — v1 goal and phases
- [field-notes/decisions.md](field-notes/decisions.md) — tech choices and why

## Running it (on the Pi)

```sh
sudo apt install -y python3-picamera2 git
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install -r requirements.txt

wget -O hand_landmarker.task \
  https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task

python capture.py      # camera only
python landmarks.py    # 21 landmarks drawn
python bench.py        # FPS, latency, CPU, jitter
python gestures.py     # pinch events on stdout
```

