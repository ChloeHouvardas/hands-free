"""Tests for the layer that puts bytes on the wire.

Split in two, by what they need:

* Everything up to "does it reach the socket" runs anywhere, by injecting a
  `socketpair()` where the L2CAP socket would be. That covers the HIDP framing,
  what happens when the host vanishes mid-report, and whether a reconnect can
  inherit a held button — none of which need Bluetooth, a Mac, or root.
* The rest needs the Pi, and says so instead of quietly passing.

The framing is worth pinning down because it is invisible when wrong: a report
missing its `0xA1` HIDP header is a perfectly well-formed *something*, and the
host simply ignores it. Nothing errors, nothing logs, the cursor just doesn't
move.

    python3 test_transport.py                    the portable half
    sudo venv/bin/python test_transport.py       adds the Pi-only half
"""

import os
import socket
import subprocess
import sys

from handsfree import hid

TESTS = []
PI_ONLY = []


def test(fn):
    TESTS.append(fn)
    return fn


def on_the_pi(fn):
    """A test that needs real Bluetooth hardware to mean anything."""
    PI_ONLY.append(fn)
    TESTS.append(fn)
    return fn


def is_pi():
    return sys.platform == "linux" and os.path.exists("/sys/class/bluetooth")


class FakeHost:
    """A socketpair standing in for a connected Mac.

    The Bluetooth backend never has to know: it writes to whatever socket it's
    been handed, so everything above the radio can be exercised on a laptop.
    """

    def __init__(self):
        # SOCK_DGRAM, not the default SOCK_STREAM: real L2CAP here is
        # SOCK_SEQPACKET and preserves message boundaries, and a stream socket
        # would happily glue three reports into one read — which would make the
        # framing test pass or fail for reasons that have nothing to do with us.
        # (AF_UNIX SOCK_SEQPACKET isn't available on macOS; DGRAM is, and keeps
        # the boundaries.)
        self.ours, self.theirs = socket.socketpair(socket.AF_UNIX,
                                                   socket.SOCK_DGRAM)
        self.ours.settimeout(1.0)

    def received(self):
        try:
            return self.ours.recv(65536)
        except (TimeoutError, OSError):
            return b""

    def vanish(self):
        self.ours.close()

    def close(self):
        for s in (self.ours, self.theirs):
            try:
                s.close()
            except OSError:
                pass


def backend_with(host):
    """A Bluetooth backend wired to a fake host, with no BlueZ involved."""
    from handsfree.transport import bluetooth

    wire = bluetooth.Backend.__new__(bluetooth.Backend)   # skip __init__/D-Bus
    import threading
    wire._lock = threading.Lock()
    wire._closed = False
    wire.verbose = False
    wire._ctrl = None
    wire._intr = host.theirs if host else None
    wire._peer = "00:11:22:33:44:55"
    return wire


# -- framing ----------------------------------------------------------------

@test
def test_every_report_carries_the_hidp_input_header():
    """0xA1 = DATA | INPUT. Without it the host ignores the report in silence."""
    from handsfree.transport.bluetooth import HIDP_INPUT

    host = FakeHost()
    try:
        wire = backend_with(host)
        report = hid.mouse_report(x=5, report_id=hid.MOUSE_ID)
        wire.send_mouse(report)
        got = host.received()
        assert got[0] == HIDP_INPUT, f"header is 0x{got[0]:02x}, want 0xa1"
        assert got[1:] == report, got.hex()
    finally:
        host.close()


@test
def test_keyboard_reports_go_out_the_same_channel_with_the_same_header():
    from handsfree.transport.bluetooth import HIDP_INPUT

    host = FakeHost()
    try:
        wire = backend_with(host)
        report = hid.keyboard_report(modifiers=1, keys=[0x50],
                                     report_id=hid.KEYBOARD_ID)
        wire.send_keyboard(report)
        got = host.received()
        assert got[0] == HIDP_INPUT and got[1:] == report, got.hex()
    finally:
        host.close()


@test
def test_reports_are_not_coalesced_into_one_packet():
    """L2CAP here is SOCK_SEQPACKET — one report per packet. If two ever got
    concatenated the host would read the second as garbage."""
    host = FakeHost()
    try:
        wire = backend_with(host)
        for i in (1, 2, 3):
            wire.send_mouse(hid.mouse_report(x=i, report_id=hid.MOUSE_ID))
        seen = [host.received() for _ in range(3)]
        # 9 bytes each: the 0xA1 HIDP header, the report ID, then the 7-byte
        # mouse report. Byte 3 is the low half of the X delta.
        assert all(len(p) == 9 for p in seen), [len(p) for p in seen]
        assert [p[3] for p in seen] == [1, 2, 3], [p.hex() for p in seen]
    finally:
        host.close()


# -- the host going away ----------------------------------------------------

@test
def test_a_vanished_host_does_not_raise():
    """The Mac closing its lid must not take the vision loop down with it."""
    host = FakeHost()
    try:
        wire = backend_with(host)
        host.vanish()
        for _ in range(20):
            wire.send_mouse(hid.mouse_report(x=1, report_id=hid.MOUSE_ID))
    finally:
        host.close()


@test
def test_a_vanished_host_is_noticed():
    host = FakeHost()
    try:
        wire = backend_with(host)
        assert wire.connected
        host.vanish()
        for _ in range(20):
            wire.send_mouse(hid.mouse_report(x=1, report_id=hid.MOUSE_ID))
        assert not wire.connected, \
            "still reporting connected after the host went away"
    finally:
        host.close()


@test
def test_sending_with_no_host_is_a_no_op_not_an_error():
    wire = backend_with(None)
    wire.send_mouse(hid.mouse_report(x=1, report_id=hid.MOUSE_ID))
    wire.send_keyboard(hid.keyboard_report(report_id=hid.KEYBOARD_ID))


@test
def test_a_dropped_host_makes_the_driver_let_go_of_the_button():
    """The reconnect hazard: if the driver still believed a button was held,
    the next session would inherit it."""
    from handsfree.driver import Driver
    from handsfree.gestures import Event

    host = FakeHost()
    try:
        wire = backend_with(host)
        driver = Driver(wire, {"watchdog": 0, "max_drag": 0}, thread=False)
        driver.handle([Event("click_down")])
        assert driver.buttons == 1

        host.vanish()
        wire.send_mouse(hid.mouse_report(report_id=hid.MOUSE_ID))  # notice it
        driver.pump()
        assert driver.buttons == 0, "button survived the host disappearing"
        assert driver.released_by == "disconnect", driver.released_by
    finally:
        host.close()


# -- the usb backend --------------------------------------------------------

@test
def test_usb_treats_eagain_as_normal():
    """EAGAIN on /dev/hidg* means no host has enumerated us — the Mac is
    asleep, or nothing is plugged in. Dropping the report is correct; raising
    would kill the vision loop every time the lid shut."""
    from handsfree.transport.usb import Backend

    class Refuses:
        def write(self, _data):
            raise BlockingIOError(11, "Resource temporarily unavailable")

        def close(self):
            pass

    Backend._send(Refuses(), b"\x00" * 7)


@test
def test_usb_survives_the_device_node_going_away():
    from handsfree.transport.usb import Backend

    class Gone:
        def write(self, _data):
            raise OSError(19, "No such device")

        def close(self):
            pass

    Backend._send(Gone(), b"\x00" * 7)


@test
def test_the_two_backends_disagree_about_report_ids_on_purpose():
    """Bluetooth shares one descriptor and needs IDs; USB gets a function per
    device and must not have them. Swapping these makes the host ignore
    everything, silently."""
    from handsfree.transport import bluetooth, usb

    assert bluetooth.Backend.mouse_id == hid.MOUSE_ID
    assert bluetooth.Backend.keyboard_id == hid.KEYBOARD_ID
    assert usb.Backend.mouse_id is None
    assert usb.Backend.keyboard_id is None


@test
def test_the_service_record_embeds_the_descriptor_we_generate():
    """Portable half of the same question the Pi asks over the air: the XML we
    hand BlueZ has to actually contain our descriptor, in attribute 0x0206."""
    from handsfree.transport.bluetooth import service_record

    for pointer in hid.POINTERS:
        desc = hid.combined_descriptor(pointer)
        record = service_record("Hands-Free", desc)
        assert desc.hex() in record, f"{pointer} descriptor missing from the record"
        assert 'id="0x0206"' in record, "no HIDDescriptorList attribute"
        assert 'value="0x1124"' in record, "not declared as a HID service"


# -- the factory ------------------------------------------------------------

@test
def test_an_unknown_backend_names_the_ones_that_exist():
    from handsfree import transport

    try:
        transport.open("carrier-pigeon")
    except ValueError as e:
        assert "bluetooth" in str(e) and "null" in str(e), e
        return
    raise AssertionError("a typo in config.toml should say what's valid")


@test
def test_the_null_backend_needs_nothing_at_all():
    from handsfree import transport

    wire = transport.open("null", {}, quiet=True)
    wire.send_mouse(hid.mouse_report())
    assert wire.sent
    wire.close()


# -- the Pi-only half -------------------------------------------------------

def sdp_records(xml=False):
    """What we're advertising right now, as sdptool sees it.

    `--xml` is the only form that includes the raw HID descriptor blob —
    plain `browse` prints the parsed attributes and drops attribute 0x0206
    entirely, so a descriptor check against it can never succeed.
    """
    argv = ["sdptool", "browse", "local"]
    if xml:
        argv.insert(2, "--xml")
    return subprocess.run(argv, capture_output=True, text=True).stdout


class Advertising:
    """Bring the real transport up, so the SDP tests test the real thing.

    The HID profile lives only as long as the D-Bus connection that registered
    it, so checking for it without holding one open would only ever tell you
    whether some *other* handsfree process happened to be running.
    """

    def __enter__(self):
        from handsfree.transport.bluetooth import Backend
        from handsfree.config import load_config
        self.wire = Backend(load_config().get("hid", {}), verbose=False)
        return self.wire

    def __exit__(self, *exc):
        self.wire.close()


@on_the_pi
def test_the_sdp_record_is_actually_published():
    """`sdptool browse local` without root shows nothing and exits 0 — it
    doesn't error, it lies. So this has to run as root to mean anything."""
    if os.geteuid() != 0:
        raise AssertionError("needs root, or sdptool reports an empty list")
    with Advertising():
        out = sdp_records()
    assert "Human Interface Device" in out, \
        "brought the transport up and still no HID record on the air"


@on_the_pi
def test_the_published_descriptor_is_the_one_we_generate():
    """macOS caches this. If what's on the air isn't what hid.py produces,
    every byte-level test in this repo is describing something else."""
    if os.geteuid() != 0:
        raise AssertionError("needs root")
    with Advertising():
        out = sdp_records(xml=True)
    flat = out.replace(" ", "").replace("\n", "").lower()
    for pointer in hid.POINTERS:
        if hid.combined_descriptor(pointer).hex() in flat:
            return
    raise AssertionError("the published HID descriptor doesn't match any "
                         "descriptor hid.py generates")


@on_the_pi
def test_the_profile_is_released_when_we_close():
    """Two runs in a row have to work — the second would fail to register if
    the first never let go."""
    if os.geteuid() != 0:
        raise AssertionError("needs root")
    with Advertising():
        pass
    with Advertising():
        assert "Human Interface Device" in sdp_records()


@on_the_pi
def test_the_hid_ports_are_bindable():
    """PSM 17 and 19 are free only while BlueZ's input plugin is disabled."""
    for psm in (17, 19):
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET,
                             socket.BTPROTO_L2CAP)
        try:
            sock.bind((socket.BDADDR_ANY, psm))
        except OSError as e:
            raise AssertionError(
                f"PSM {psm}: {e} — either a handsfree process already holds "
                "it, or bluetoothd still has its input plugin loaded")
        finally:
            sock.close()


@on_the_pi
def test_the_adapter_advertises_itself_as_a_pointing_device():
    out = subprocess.run(["hciconfig", "hci0", "class"],
                         capture_output=True, text=True).stdout
    assert "Combo keyboard/pointing" in out or "Pointing" in out, out


@on_the_pi
def test_the_adapter_is_not_also_advertising_itself_as_a_speaker():
    """Audio profiles alongside HID make macOS file the device under Sound and
    negotiate A2DP instead of HID. Fixed by dropping those plugins too."""
    if os.geteuid() != 0:
        raise AssertionError("needs root")
    out = sdp_records()
    # A2DP/AVRCP/Audio Sink are the ones that made macOS file the device under
    # Sound and negotiate audio instead of input, and dropping the bluetoothd
    # plugins removes them. HFP ("Hands-Free ...") is deliberately not in this
    # list: it comes from PipeWire registering over the BlueZ Media API, not
    # from any bluetoothd plugin, so no -P flag can shift it. It's reported by
    # `pair --check` as a note rather than treated as a failure — if the manual
    # pass shows macOS misbehaving, mask pipewire on the Pi.
    noisy = [name for name in ("Audio Source", "Audio Sink", "AVRCP")
             if name in out]
    assert not noisy, \
        f"still advertising {', '.join(noisy)} — this is a mouse, not a " \
        "speaker. Run `pair --setup` to drop the audio plugins."


def main():
    pi = is_pi()
    skipped = 0
    failed = []
    for fn in TESTS:
        if fn in PI_ONLY and not pi:
            print(f"  skip  {fn.__name__}  (needs the Pi)")
            skipped += 1
            continue
        try:
            fn()
            print(f"  pass  {fn.__name__}")
        except AssertionError as e:
            failed.append(fn.__name__)
            print(f"  FAIL  {fn.__name__}\n          {e}")
        except Exception as e:
            failed.append(fn.__name__)
            print(f"  ERROR {fn.__name__}\n          {type(e).__name__}: {e}")
    ran = len(TESTS) - skipped
    print(f"\n{ran - len(failed)}/{ran} pass"
          + (f", {skipped} skipped (not the Pi)" if skipped else ""))
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
