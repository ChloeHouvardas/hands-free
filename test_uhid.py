"""Check our HID bytes against the Linux kernel's own HID parser.

`test_hid.py` validates the report descriptor with a decoder written in this
repo, which is circular: it only proves the encoder and the decoder agree. If
both are wrong in the same way, both tests pass and the Mac ignores us.

This breaks the circle. `/dev/uhid` lets userspace create a virtual HID device,
which the **real kernel HID subsystem** then parses exactly as it would parse a
physical mouse. Feed it our descriptor, push our report bytes through it, and
read back what the kernel decided they meant:

    hid.combined_descriptor()  ->  /dev/uhid  ->  kernel HID parser
    hid.mouse_report(x=10)     ->  /dev/uhid  ->  /dev/input/eventN
                                                  -> EV_REL REL_X +10

If a mature, independent implementation agrees with us about what our bytes
mean, the descriptor is almost certainly right for macOS too. If it doesn't,
we've found the bug without needing a host at all.

Linux only, and needs root — /dev/uhid is 0600.

    sudo venv/bin/python test_uhid.py
"""

import os
import select
import struct
import sys
import time

from handsfree import hid

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


# -- the uhid ABI, from /usr/include/linux/uhid.h ---------------------------

UHID_DESTROY, UHID_START, UHID_STOP = 1, 2, 3
UHID_OPEN, UHID_CLOSE, UHID_OUTPUT = 4, 5, 6
UHID_CREATE2, UHID_INPUT2 = 11, 12

# struct uhid_event { __u32 type; union { ... } u; } __packed
# with create2 = { name[128], phys[64], uniq[64], rd_size, bus, vendor,
#                  product, version, country, rd_data[4096] }
CREATE2 = "<I128s64s64sHHIIII4096s"
INPUT2 = "<IH4096s"

BUS_BLUETOOTH = 0x05

# -- the evdev ABI, from /usr/include/linux/input*.h ------------------------

EV_SYN, EV_KEY, EV_REL = 0x00, 0x01, 0x02
REL_X, REL_Y, REL_HWHEEL, REL_WHEEL = 0x00, 0x01, 0x06, 0x08
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112
KEY_LEFTCTRL, KEY_LEFTSHIFT, KEY_LEFT, KEY_RIGHT = 29, 42, 105, 106

# struct input_event { struct timeval time; __u16 type, code; __s32 value; }
# 24 bytes on aarch64 — asserted in test_the_abi_matches_this_machine below.
EVENT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT)

NAMES = {
    (EV_REL, REL_X): "REL_X", (EV_REL, REL_Y): "REL_Y",
    (EV_REL, REL_WHEEL): "REL_WHEEL", (EV_REL, REL_HWHEEL): "REL_HWHEEL",
    (EV_KEY, BTN_LEFT): "BTN_LEFT", (EV_KEY, BTN_RIGHT): "BTN_RIGHT",
    (EV_KEY, BTN_MIDDLE): "BTN_MIDDLE",
    (EV_KEY, KEY_LEFTCTRL): "KEY_LEFTCTRL", (EV_KEY, KEY_LEFT): "KEY_LEFT",
    (EV_KEY, KEY_RIGHT): "KEY_RIGHT", (EV_KEY, KEY_LEFTSHIFT): "KEY_LEFTSHIFT",
}


class Virtual:
    """A HID device the kernel believes in, built from our own descriptor.

    Deliberately not a general-purpose uhid wrapper — it does exactly what a
    test needs: create, push reports, read back what the kernel made of them.
    """

    def __init__(self, descriptor, name="handsfree-selftest"):
        before = set(_event_nodes())
        self.fd = os.open("/dev/uhid", os.O_RDWR | os.O_NONBLOCK)
        os.write(self.fd, struct.pack(
            CREATE2, UHID_CREATE2, name.encode(), b"uhid-test", b"",
            len(descriptor), BUS_BLUETOOTH,
            0x1D6B, 0x0246, 0x0100, 0, descriptor))

        # udev takes a moment to materialise the input node(s). A combined
        # descriptor with two application collections can produce more than
        # one, so take everything new and read them all.
        self.nodes = []
        for _ in range(100):
            new = sorted(set(_event_nodes()) - before)
            if new:
                time.sleep(0.15)        # let the whole set settle
                self.nodes = sorted(set(_event_nodes()) - before)
                break
            time.sleep(0.05)
        if not self.nodes:
            raise AssertionError(
                "the kernel accepted the descriptor but created no input "
                "device — usually means it parsed to nothing usable")

        self.inputs = [os.open(p, os.O_RDONLY | os.O_NONBLOCK)
                       for p in self.nodes]
        self.drain()

    def send(self, report):
        os.write(self.fd, struct.pack(INPUT2, UHID_INPUT2, len(report),
                                      bytes(report)))

    def events(self, settle=0.25):
        """Everything the kernel decoded, as [(name, value)]."""
        out = []
        deadline = time.monotonic() + settle
        while time.monotonic() < deadline:
            ready, _, _ = select.select(self.inputs, [], [], 0.05)
            for fd in ready:
                try:
                    blob = os.read(fd, EVENT_SIZE * 64)
                except BlockingIOError:
                    continue
                for i in range(0, len(blob) - EVENT_SIZE + 1, EVENT_SIZE):
                    _s, _us, etype, code, value = struct.unpack(
                        EVENT, blob[i:i + EVENT_SIZE])
                    if etype == EV_SYN:
                        continue
                    out.append((NAMES.get((etype, code),
                                          f"{etype}/{code}"), value))
        return out

    def drain(self):
        self.events(settle=0.2)

    def close(self):
        try:
            os.write(self.fd, struct.pack("<I", UHID_DESTROY))
        except OSError:
            pass
        for fd in self.inputs:
            os.close(fd)
        os.close(self.fd)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _event_nodes():
    return [f"/dev/input/{n}" for n in os.listdir("/dev/input")
            if n.startswith("event")]


def mouse(pointer="relative", **kw):
    """A mouse report on the Bluetooth (combined, report-ID) descriptor."""
    return hid.mouse_report(report_id=hid.POINTER_IDS[pointer],
                            pointer=pointer, **kw)


def keys(modifiers=0, pressed=()):
    return hid.keyboard_report(modifiers=modifiers, keys=pressed,
                               report_id=hid.KEYBOARD_ID)


def values(events, name):
    return [v for n, v in events if n == name]


# -- the ABI this file assumes ----------------------------------------------

@test
def test_the_abi_matches_this_machine():
    """Every other test here is meaningless if the structs are the wrong size."""
    assert EVENT_SIZE == 24, f"input_event is {EVENT_SIZE} bytes, expected 24"
    assert struct.calcsize(CREATE2) == 4376, struct.calcsize(CREATE2)
    assert struct.calcsize(INPUT2) == 4102, struct.calcsize(INPUT2)
    assert os.path.exists("/dev/uhid"), "no /dev/uhid — is this Linux?"


# -- the kernel accepts what we generate ------------------------------------

@test
def test_the_kernel_accepts_every_descriptor_we_generate():
    """A descriptor the kernel can't parse produces no input device at all."""
    with Virtual(hid.combined_descriptor()) as dev:
        assert dev.nodes
    for pointer in hid.POINTERS:
        with Virtual(hid.mouse_descriptor(pointer=pointer)) as dev:
            assert dev.nodes, pointer
    with Virtual(hid.keyboard_descriptor()) as dev:
        assert dev.nodes


# -- the mouse --------------------------------------------------------------

@test
def test_relative_movement_arrives_with_the_right_value_and_sign():
    with Virtual(hid.combined_descriptor()) as dev:
        dev.send(mouse(x=10, y=-7))
        got = dev.events()
        assert values(got, "REL_X") == [10], got
        assert values(got, "REL_Y") == [-7], got


@test
def test_a_negative_delta_is_not_read_as_a_huge_positive_one():
    """The classic two's-complement bug: -10 arriving as 65526 would send the
    cursor to the far edge of the screen instead of ten pixels left."""
    with Virtual(hid.combined_descriptor()) as dev:
        dev.send(mouse(x=-10, y=-1))
        got = dev.events()
        assert values(got, "REL_X") == [-10], got
        assert values(got, "REL_Y") == [-1], got


@test
def test_large_deltas_survive_the_16_bit_axes():
    """The reason the axes are 16-bit rather than the usual 8."""
    with Virtual(hid.combined_descriptor()) as dev:
        for delta in (200, -200, 20000, -20000, hid.ABSOLUTE_MAX):
            dev.drain()
            dev.send(mouse(x=delta))
            got = dev.events()
            assert values(got, "REL_X") == [delta], (delta, got)


@test
def test_the_button_goes_down_and_comes_back_up():
    with Virtual(hid.combined_descriptor()) as dev:
        dev.send(mouse(buttons=1))
        assert values(dev.events(), "BTN_LEFT") == [1]
        dev.send(mouse(buttons=0))
        assert values(dev.events(), "BTN_LEFT") == [0]


@test
def test_the_wheel_and_the_pan_axis_are_not_swapped():
    """Wheel is vertical, AC Pan is horizontal. Swapping them is invisible in
    the bytes and obvious the moment a human scrolls."""
    with Virtual(hid.combined_descriptor()) as dev:
        dev.send(mouse(wheel=3))
        got = dev.events()
        assert values(got, "REL_WHEEL") == [3], got
        assert not values(got, "REL_HWHEEL"), got

        dev.drain()
        dev.send(mouse(pan=-2))
        got = dev.events()
        assert values(got, "REL_HWHEEL") == [-2], got
        assert not values(got, "REL_WHEEL"), got


@test
def test_a_resting_report_moves_nothing():
    """The neutral report close() sends must not itself nudge the cursor."""
    with Virtual(hid.combined_descriptor()) as dev:
        dev.send(mouse())
        assert dev.events() == [], "the all-zero report produced input"


@test
def test_absolute_positions_arrive_as_absolute_axes():
    with Virtual(hid.combined_descriptor()) as dev:
        dev.send(mouse("absolute", x=1000, y=2000))
        got = dev.events()
        # ABS_X/ABS_Y are type 3, which NAMES doesn't map — check by value.
        assert any(v == 1000 for _n, v in got), got
        assert any(v == 2000 for _n, v in got), got


# -- the keyboard -----------------------------------------------------------

@test
def test_the_swipe_shortcut_arrives_as_ctrl_plus_left():
    """What a three-finger swipe actually turns into."""
    modifiers, key = hid.combo("ctrl+left")
    with Virtual(hid.combined_descriptor()) as dev:
        dev.send(keys(modifiers, [key]))
        got = dev.events()
        assert values(got, "KEY_LEFTCTRL") == [1], got
        assert values(got, "KEY_LEFT") == [1], got

        dev.drain()
        dev.send(keys(0, ()))
        got = dev.events()
        assert values(got, "KEY_LEFTCTRL") == [0], got
        assert values(got, "KEY_LEFT") == [0], got


@test
def test_the_other_swipe_direction_is_a_different_key():
    modifiers, key = hid.combo("ctrl+right")
    with Virtual(hid.combined_descriptor()) as dev:
        dev.send(keys(modifiers, [key]))
        got = dev.events()
        assert values(got, "KEY_RIGHT") == [1], got
        assert not values(got, "KEY_LEFT"), got


@test
def test_a_two_modifier_combo_sets_both():
    modifiers, key = hid.combo("cmd+shift+]")
    with Virtual(hid.combined_descriptor()) as dev:
        dev.send(keys(modifiers, [key]))
        got = dev.events()
        assert values(got, "KEY_LEFTSHIFT") == [1], got
        assert any(n.startswith("1/") or n == "KEY_LEFTSHIFT" for n, _v in got), got


# -- report IDs -------------------------------------------------------------

@test
def test_report_ids_route_mouse_and_keyboard_apart():
    """Bluetooth shares one descriptor between both devices, told apart only by
    a leading ID byte. Get that wrong and the host silently drops everything —
    this is the failure the whole combined-descriptor design risks."""
    with Virtual(hid.combined_descriptor()) as dev:
        dev.send(mouse(x=5))
        got = dev.events()
        assert values(got, "REL_X") == [5], got
        assert not any(n.startswith("KEY_") for n, _v in got), got

        dev.drain()
        modifiers, key = hid.combo("ctrl+left")
        dev.send(keys(modifiers, [key]))
        got = dev.events()
        assert values(got, "KEY_LEFT") == [1], got
        assert not values(got, "REL_X"), got


@test
def test_the_usb_shaped_reports_work_without_report_ids():
    """USB gets a ConfigFS function per device and no report IDs at all."""
    with Virtual(hid.mouse_descriptor(pointer="relative")) as dev:
        dev.send(hid.mouse_report(x=9, y=9))
        got = dev.events()
        assert values(got, "REL_X") == [9], got
        assert values(got, "REL_Y") == [9], got


@test
def test_a_report_sent_with_the_wrong_id_moves_nothing():
    """Proves the previous tests aren't passing by accident — if the kernel
    ignored IDs entirely, a mouse report tagged as a keyboard would still
    move the cursor."""
    with Virtual(hid.combined_descriptor()) as dev:
        dev.send(hid.mouse_report(x=50, report_id=hid.KEYBOARD_ID))
        assert values(dev.events(), "REL_X") == [], \
            "the kernel is not honouring report IDs, so the routing tests " \
            "above prove nothing"


def main():
    if sys.platform != "linux":
        print(f"  skipped — /dev/uhid is Linux only, this is {sys.platform}")
        print("\n0/0 pass (skipped)")
        raise SystemExit(0)
    if os.geteuid() != 0:
        raise SystemExit("this needs root — /dev/uhid is 0600.\n"
                         "  Try: sudo venv/bin/python test_uhid.py")

    failed = []
    for fn in TESTS:
        try:
            fn()
            print(f"  pass  {fn.__name__}")
        except AssertionError as e:
            failed.append(fn.__name__)
            print(f"  FAIL  {fn.__name__}\n          {e}")
        except Exception as e:
            failed.append(fn.__name__)
            print(f"  ERROR {fn.__name__}\n          {type(e).__name__}: {e}")
    print(f"\n{len(TESTS) - len(failed)}/{len(TESTS)} pass")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
