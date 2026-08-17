# hands-free

A plug-in device that watches your hand and acts as a mouse. A Raspberry Pi with
a camera runs hand tracking locally and speaks to the host as a USB HID mouse —
no driver, no app.

Early prototype. Docs and code walkthrough: [docs/index.md](docs/index.md).

## Running it (on the Pi)

```sh
sudo apt install -y python3-picamera2 git
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install -r requirements.txt

wget -O hand_landmarker.task \
  https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task

python3 -m handsfree            # the list of commands
python3 -m handsfree capture    # camera only
python3 -m handsfree landmarks  # 21 landmarks drawn
python3 -m handsfree bench      # FPS, latency, CPU, jitter
python3 -m handsfree run        # gesture events on stdout
```

