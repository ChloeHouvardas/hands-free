# hands-free

A device that watches your hand and acts as a mouse. A Raspberry Pi with a
camera runs hand tracking locally and pairs with the host as a Bluetooth HID
mouse and keyboard — no driver, no app, nothing installed on the host.

Early prototype. Docs and code walkthrough: [docs/index.md](docs/index.md).

## Running it (on the Pi)

```sh
sudo apt install -y python3-picamera2 python3-dbus python3-gi git
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install -r requirements.txt

wget -O hand_landmarker.task \
  https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task

python3 -m handsfree            # the list of commands
python3 -m handsfree capture    # camera only
python3 -m handsfree landmarks  # 21 landmarks drawn
python3 -m handsfree bench      # FPS, latency, CPU, jitter

sudo venv/bin/python -m handsfree pair --setup   # once — reconfigures bluetoothd
sudo venv/bin/python -m handsfree pair           # advertise, then pair from the host
sudo venv/bin/python -m handsfree run            # the whole thing
```

Root is needed because Bluetooth HID listens on L2CAP PSM 17 and 19, which are
privileged. `run --transport null` prints reports instead of sending them, and
needs neither root nor pairing.

`pair --setup` disables BlueZ's `input` plugin, so **the Pi can no longer use
Bluetooth mice or keyboards itself.** It's one now.

## Working on it (on any laptop, no hardware)

The gesture layer, the report encoding and the driver import nothing
hardware — not even numpy — so all of this runs anywhere:

```sh
python3 test_gestures.py
python3 test_hid.py
python3 test_driver.py
python3 -m handsfree replay 'recordings/*.jsonl' --check --drive
```

