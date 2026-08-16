# Hardware setup

Pi 4 (2GB) + Camera Module 3, headless, driven from a Mac.

Hostname `chloespie`, user `chloewashere` — substitute your own throughout.

## 1. Flash the SD card

- Install the imager: `brew install --cask raspberry-pi-imager`
- **Device** → Raspberry Pi 4
- **OS** → `Raspberry Pi OS (other)` → `Raspberry Pi OS (Legacy, 64-bit)`
  - This is Bookworm / Debian 12. Confirm the description says so
  - Must be **64-bit**, not the 32-bit legacy entry
  - Bookworm, not Trixie: Trixie ships Python 3.13 and MediaPipe has no 3.13 wheel
- **Storage** → the SD card
- **Next → Edit Settings** — do not skip, a headless Pi is unreachable without it:
  - hostname
  - username + password
  - Wi-Fi SSID, password, country
  - **Services → Enable SSH**, password authentication
- Write, and let it verify

## 2. Attach the camera

- Pi **unplugged** before touching the ribbon
- Camera Module 3 goes in the `CAMERA` port (the narrow one between the HDMI
  ports and the audio jack), not the `DISPLAY` port
- Lift the black tab, insert the ribbon with the blue backing facing the
  USB/Ethernet side, press the tab back down
- If the camera isn't detected later, the ribbon is almost certainly reversed

## 3. Boot

- Insert the SD card, connect power
- Wait a few minutes for first boot — it resizes the filesystem and joins Wi-Fi

## 4. SSH from the Mac

```sh
ssh chloewashere@chloespie.local
```

Reflashing generates a new host key, so a previously-connected Mac refuses to
connect with `REMOTE HOST IDENTIFICATION HAS CHANGED`. Expected, not an attack:

```sh
ssh-keygen -R chloespie.local
```

Then reconnect and accept the new fingerprint.

## 5. Verify

```sh
cat /etc/os-release | head -2     # bookworm / Debian 12
dpkg --print-architecture          # arm64
python3 --version                  # 3.11.x
libcamera-hello --timeout 2000     # camera alive
```

`dpkg --print-architecture` is the one that matters for pip wheels — a 64-bit
kernel can run a 32-bit userland, so `uname -m` can mislead.

## 6. Raspberry Pi Connect

On the Pi, over SSH:

```sh
rpi-connect on
rpi-connect signin
```

- `signin` prints a URL and code — open it on the Mac, sign in with your
  Raspberry Pi ID, and the device links itself
- Preinstalled on Desktop and Full images. If the command is missing you flashed
  Lite: `sudo apt install rpi-connect`

## 7. Screen share

- Go to [connect.raspberrypi.com](https://connect.raspberrypi.com)
- Pick the Pi → **Screen Sharing**
- Needs Wayland, which Bookworm uses by default. If the option is greyed out:
  `sudo raspi-config` → Advanced Options → Wayland
- Not available on Lite images

Use the shell for installs and benchmarks, screen share only for preview
windows — the desktop session costs real CPU on a 2GB Pi.
