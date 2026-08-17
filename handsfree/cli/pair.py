"""Set the Pi up as a Bluetooth HID device, and pair a Mac with it.

    sudo python3 -m handsfree pair --setup    once, changes bluetoothd
    sudo python3 -m handsfree pair            advertise and wait for a Mac
    sudo python3 -m handsfree pair --check    just say what's wrong

`--setup` is the part that needs doing exactly once per Pi. It disables
BlueZ's `input` plugin, which is the HID *host* and which holds the two L2CAP
ports a HID *device* has to listen on, and sets the adapter's class so a Mac
shows it as a keyboard and mouse rather than as something to play audio to.
"""

import os
import re
import subprocess
import sys
import time

from handsfree.cli import parser
from handsfree.config import load_config

OVERRIDE = "/etc/systemd/system/bluetooth.service.d/override.conf"
MAIN_CONF = "/etc/bluetooth/main.conf"

#: Plugins to drop. `input` is the one that must go — it's BlueZ's HID *host*
#: and it holds L2CAP PSM 17 and 19, so while it's loaded our bind fails with
#: EADDRINUSE. The rest are hygiene: leaving a2dp/avrcp/midi/health enabled
#: makes the Pi advertise Audio Source, Audio Sink, AVRCP and Hands-Free
#: records alongside HID, and macOS then files the device under Sound and
#: negotiates audio instead of input. This is a mouse, not a speaker.
NOPLUGIN = "input,a2dp,avrcp,midi,health"

OVERRIDE_BODY = f"""[Service]
# Written by `python3 -m handsfree pair --setup`.
#
# --compat exposes the deprecated SDP socket, which is how `sdptool` (and so
# `pair --check`) can read back what we're actually advertising.
#
# -P drops plugins. Cost of dropping `input`: this Pi can no longer use
# Bluetooth mice or keyboards itself. It is one now.
ExecStart=
ExecStart=/usr/libexec/bluetooth/bluetoothd --compat -P {NOPLUGIN}
"""


def _root():
    if os.geteuid() != 0:
        raise SystemExit("this needs root — L2CAP PSM 17 and 19 are "
                         "privileged ports.\n  Try: sudo python3 -m handsfree "
                         "pair")


def setup():
    """Reconfigure bluetoothd. Idempotent; safe to run again."""
    _root()
    changed = False

    os.makedirs(os.path.dirname(OVERRIDE), exist_ok=True)
    if not os.path.exists(OVERRIDE) or \
            open(OVERRIDE).read() != OVERRIDE_BODY:
        with open(OVERRIDE, "w") as fh:
            fh.write(OVERRIDE_BODY)
        print(f"  wrote {OVERRIDE}")
        changed = True

    conf = open(MAIN_CONF).read()
    before = conf
    # 0x0025C0: limited discoverable, peripheral major, combo keyboard/pointing
    # minor. Read-only over D-Bus in BlueZ 5.66, so it has to be set here.
    conf = re.sub(r"^#?\s*Class = .*$", "Class = 0x0025C0", conf, count=1,
                  flags=re.M)
    conf = re.sub(r"^#?\s*DiscoverableTimeout = .*$", "DiscoverableTimeout = 0",
                  conf, count=1, flags=re.M)
    if conf != before:
        if not os.path.exists(MAIN_CONF + ".orig"):
            with open(MAIN_CONF + ".orig", "w") as fh:
                fh.write(before)
        with open(MAIN_CONF, "w") as fh:
            fh.write(conf)
        print(f"  set Class and DiscoverableTimeout in {MAIN_CONF}")
        changed = True

    if changed:
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "restart", "bluetooth"], check=True)
        time.sleep(2)
        print("  restarted bluetoothd")
    else:
        print("  already set up, nothing to do")
    return changed


def check():
    """Report on everything that has to be true, without changing any of it."""
    ok = True

    def line(good, label, detail=""):
        nonlocal ok
        ok = ok and good
        print(f"  {'ok  ' if good else 'FAIL'}  {label}"
              f"{'   ' + detail if detail else ''}")

    try:
        argv = subprocess.run(["systemctl", "show", "bluetooth",
                               "-p", "ExecStart"], capture_output=True,
                              text=True).stdout
    except FileNotFoundError:
        argv = ""
    line(f"-P {NOPLUGIN}" in argv,
         "bluetoothd has the input and audio plugins disabled",
         "" if f"-P {NOPLUGIN}" in argv else "run with --setup")

    klass = ""
    try:
        out = subprocess.run(["hciconfig", "hci0", "class"],
                             capture_output=True, text=True).stdout
        klass = (re.search(r"Class: (0x[0-9a-f]+)", out) or [None, ""])[1]
        minor = int(klass, 16) & 0xFFFF if klass else 0
    except (FileNotFoundError, ValueError):
        minor = 0
    line(minor & 0x05C0 == 0x05C0, "adapter class is keyboard/pointing", klass)

    import socket
    for psm in (17, 19):
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET,
                             socket.BTPROTO_L2CAP)
        try:
            sock.bind((socket.BDADDR_ANY, psm))
            line(True, f"L2CAP PSM {psm} is bindable")
        except PermissionError:
            line(False, f"L2CAP PSM {psm}", "needs root")
        except OSError as e:
            line(False, f"L2CAP PSM {psm}", f"{e} — is another run alive?")
        finally:
            sock.close()

    try:
        import dbus                                              # noqa: F401
        line(True, "python3-dbus is importable")
    except ImportError:
        line(False, "python3-dbus", "apt install python3-dbus python3-gi")

    # Not a failure, but worth knowing about. HFP is registered by PipeWire
    # over the BlueZ Media API rather than by a bluetoothd plugin, so -P can't
    # remove it and the Mac may still list the device under audio services.
    try:
        sdp = subprocess.run(["sdptool", "browse", "local"],
                             capture_output=True, text=True).stdout
        if "Hands-Free" in sdp:
            print("  note  also advertising Hands-Free (HFP) — that's "
                  "PipeWire, not bluetoothd.\n"
                  "        Harmless, but `systemctl --user mask pipewire` "
                  "removes it if the Mac misbehaves.")
    except FileNotFoundError:
        pass

    return ok


def main():
    ap = parser("pair")
    ap.add_argument("--setup", action="store_true",
                    help="reconfigure bluetoothd, then exit")
    ap.add_argument("--check", action="store_true",
                    help="report what's wrong and exit")
    ap.add_argument("--name", default=None,
                    help="override the name the Mac sees")
    ap.add_argument("--timeout", type=float, default=None,
                    help="give up waiting after this many seconds")
    args = ap.parse_args()

    if args.setup:
        setup()
        print("\nNow run:  sudo python3 -m handsfree pair")
        return 0

    if args.check:
        return 0 if check() else 1

    _root()
    if not check():
        print("\nSomething above needs fixing first. "
              "Try: sudo python3 -m handsfree pair --setup")
        return 1

    cfg = load_config().get("hid", {})
    if args.name:
        cfg = dict(cfg, name=args.name)

    from handsfree.transport.bluetooth import Backend

    print()
    transport = Backend(cfg)
    print(f"\n  Advertising as '{cfg.get('name', 'Hands-Free')}' "
          f"({transport.bluez['address']}).\n"
          f"  On the Mac: System Settings > Bluetooth, and pair it.\n")

    try:
        if not transport.wait_for_host(args.timeout):
            print("  Nothing connected. Still advertising — leave this "
                  "running and try the Mac again.", file=sys.stderr)
            return 1

        print("\n  Paired and connected. Moving the cursor in a square so you "
              "can see it works.\n")
        from handsfree import hid
        for dx, dy in ((40, 0), (0, 40), (-40, 0), (0, -40)) * 3:
            for _ in range(20):
                transport.send_mouse(hid.mouse_report(
                    x=dx // 20, y=dy // 20, report_id=transport.mouse_id))
                time.sleep(0.01)
        print("  Done. If the cursor drew a square, the transport works.\n"
              "  Now run:  sudo python3 -m handsfree run\n")
        return 0
    finally:
        transport.close()


if __name__ == "__main__":
    sys.exit(main())
