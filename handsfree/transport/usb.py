"""USB HID: enumerate as a real composite keyboard and mouse.

Not usable yet — it needs a part that hasn't been ordered. Written anyway,
because a backend interface with one implementation isn't an interface, it's a
guess, and building the second one is what proves `driver.py` doesn't secretly
assume Bluetooth.

**Why it needs hardware.** The Pi 4 has exactly one port that can be a USB
device — the USB-C one — and that same port is how the board is powered. There
is no USB-PD negotiation on it, and under MediaPipe load the Pi draws more than
a laptop port advertises, so the failure mode is undervoltage throttling that
looks exactly like a MediaPipe bug. The four USB-A ports can't help: they're
behind a VL805 host controller and are host-only in silicon.

The working topology is power and data on separate paths — an 8086 Consultancy
USB-C/PWR splitter, or 5V into the GPIO pins with a VBUS-cut data cable. Not a
Y-splitter; two 5V sources back-feed each other.

**Before this can run**, `/boot/firmware/config.txt` needs `otg_mode=1` removed
and `dtoverlay=dwc2,dr_mode=host` changed to `dr_mode=peripheral`, plus
`modules-load=dwc2` in `cmdline.txt` and `libcomposite` in `/etc/modules`. That
is a reboot and a changed USB topology, so it stays undone until the splitter
is actually in hand.

Unlike Bluetooth, USB gets a ConfigFS *function* per device — so two device
nodes, two descriptors, and no report IDs.
"""

import os
import time

from handsfree import hid
from handsfree.transport import Transport

GADGET = "/sys/kernel/config/usb_gadget/handsfree"

#: Linux Foundation's vendor ID and its "multifunction composite" product ID.
#: Fine for a one-off device; don't ship a product with them.
VENDOR, PRODUCT = 0x1D6B, 0x0104


def _write(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(value)


def build_gadget(root=GADGET, serial="hands-free", manufacturer="hands-free",
                 product="Hands-Free", pointer="relative"):
    """Create the composite gadget in ConfigFS and bind it to the UDC.

    Idempotent-ish: if the gadget already exists and is bound, leave it alone.
    Tearing one down properly means unbinding the UDC first, which is what
    `teardown_gadget` is for.
    """
    if os.path.exists(os.path.join(root, "UDC")):
        with open(os.path.join(root, "UDC")) as fh:
            if fh.read().strip():
                return root             # already bound; nothing to do

    _write(f"{root}/idVendor", f"0x{VENDOR:04x}")
    _write(f"{root}/idProduct", f"0x{PRODUCT:04x}")
    _write(f"{root}/bcdDevice", "0x0100")
    _write(f"{root}/bcdUSB", "0x0200")

    _write(f"{root}/strings/0x409/serialnumber", serial)
    _write(f"{root}/strings/0x409/manufacturer", manufacturer)
    _write(f"{root}/strings/0x409/product", product)

    _write(f"{root}/configs/c.1/strings/0x409/configuration", "keyboard + mouse")
    _write(f"{root}/configs/c.1/MaxPower", "250")

    # hid.usb0 is the keyboard and becomes /dev/hidg0; hid.usb1 is the mouse
    # and becomes /dev/hidg1. Order of creation is what fixes that mapping.
    for name, descriptor, length in (
            ("usb0", hid.keyboard_descriptor(), len(hid.keyboard_report())),
            ("usb1", hid.mouse_descriptor(pointer=pointer),
             len(hid.mouse_report(pointer=pointer)))):
        fn = f"{root}/functions/hid.{name}"
        _write(f"{fn}/protocol", "1" if name == "usb0" else "2")
        _write(f"{fn}/subclass", "1")
        _write(f"{fn}/report_length", str(length))
        with open(f"{fn}/report_desc", "wb") as fh:
            fh.write(descriptor)
        link = f"{root}/configs/c.1/hid.{name}"
        if not os.path.exists(link):
            os.symlink(fn, link)

    udcs = sorted(os.listdir("/sys/class/udc"))
    if not udcs:
        raise SystemExit(
            "no USB device controller found.\n"
            "  /boot/firmware/config.txt needs dtoverlay=dwc2,dr_mode=peripheral\n"
            "  and otg_mode=1 removed, then a reboot. See this module's "
            "docstring.")
    _write(f"{root}/UDC", udcs[0])
    return root


def teardown_gadget(root=GADGET):
    """Unbind and remove the gadget, in the order ConfigFS insists on."""
    if not os.path.isdir(root):
        return
    try:
        _write(f"{root}/UDC", "\n")
    except OSError:
        pass
    for link in ("hid.usb0", "hid.usb1"):
        path = f"{root}/configs/c.1/{link}"
        if os.path.islink(path):
            os.unlink(path)
    for path in (f"{root}/configs/c.1/strings/0x409",
                 f"{root}/configs/c.1",
                 f"{root}/functions/hid.usb0",
                 f"{root}/functions/hid.usb1",
                 f"{root}/strings/0x409",
                 root):
        try:
            os.rmdir(path)
        except OSError:
            pass


class Backend(Transport):
    """Two device nodes. No report IDs — each function has its own descriptor."""

    name = "usb"
    keyboard_id = None
    mouse_id = None

    def __init__(self, cfg=None, keyboard="/dev/hidg0", mouse="/dev/hidg1",
                 build=True):
        cfg = cfg or {}
        if build:
            build_gadget(pointer=cfg.get("pointer", "relative"))
            # udev needs a moment to make the nodes after the UDC binds.
            for _ in range(50):
                if os.path.exists(keyboard) and os.path.exists(mouse):
                    break
                time.sleep(0.1)

        try:
            self._keyboard = open(keyboard, "wb", buffering=0)
            self._mouse = open(mouse, "wb", buffering=0)
        except FileNotFoundError:
            raise SystemExit(
                f"{keyboard} / {mouse} don't exist. The gadget didn't bind — "
                "see this module's docstring for the boot config it needs.")

    @property
    def connected(self):
        # There's no cheap way to ask whether a host has enumerated us; a write
        # returning EAGAIN is the signal, and that's handled per-report.
        return True

    def send_keyboard(self, report):
        self._send(self._keyboard, report)

    def send_mouse(self, report):
        self._send(self._mouse, report)

    @staticmethod
    def _send(fh, report):
        try:
            fh.write(bytes(report))
        except BlockingIOError:
            # EAGAIN here is normal and means no host is enumerated — the Mac
            # is asleep, or nothing is plugged in. Dropping the report is
            # right; the next one carries the current state anyway.
            pass
        except OSError:
            pass

    def close(self):
        for fh in (self._keyboard, self._mouse):
            try:
                fh.close()
            except OSError:
                pass
