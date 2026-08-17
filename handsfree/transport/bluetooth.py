"""Bluetooth HID: pretend to be a wireless mouse and keyboard.

The Pi 4's four USB-A ports are behind a VL805 host controller and can only
ever be hosts, so the only wired way to be a mouse is the USB-C port — which is
also the only way to power the board. Bluetooth sidesteps that entirely: no
cable, no splitter, nothing to order.

The catch is that **BlueZ has no supported HID-device mode**. It is built to
talk *to* mice, not to be one. So three things have to be arranged by hand:

1. **Its `input` plugin has to go.** That plugin is the HID *host*, and it holds
   L2CAP PSM 17 and 19 — the two ports a HID device has to listen on. While
   it's loaded our bind fails with EADDRINUSE. `bluetoothd -P input` drops it,
   at the cost of this Pi no longer being able to use Bluetooth mice itself.
2. **The SDP record has to be published manually**, because nothing in BlueZ
   advertises a HID device. `ProfileManager1.RegisterProfile` takes the whole
   record as XML, which is the supported route and avoids the deprecated
   `sdptool`.
3. **Pairing needs an agent.** A headless box has no way to show a passkey, so
   we register a NoInputNoOutput agent and accept everything.

`python3 -m handsfree pair` does the setup and checks all of it.

Two gotchas that will cost you an evening each:

* **macOS caches SDP records.** Change `hid.combined_descriptor` by one byte
  and a Mac that has already paired keeps using the old one. Forget the device
  in System Settings and pair again, or you're debugging code that isn't
  running.
* **Wi-Fi and Bluetooth are the same chip and the same antenna** on a Pi 4. The
  MJPEG preview streams over Wi-Fi. Run with `--no-preview` when it matters.
"""

import socket
import threading
import time

from handsfree import hid

HID_UUID = "00001124-0000-1000-8000-00805f9b34fb"
P_CTRL, P_INTR = 17, 19

AGENT_PATH = "/handsfree/agent"
PROFILE_PATH = "/handsfree/profile"

#: HIDP header for an input report on the interrupt channel: DATA | INPUT.
HIDP_INPUT = 0xA1

#: Peripheral / combo keyboard-pointing, plus the limited-discoverable bit.
#: Set in /etc/bluetooth/main.conf — `Class` is read-only over D-Bus in 5.66.
CLASS = 0x0025C0


def service_record(name, descriptor):
    """The SDP record a HID device has to publish, as BlueZ wants it.

    Attribute numbers are from the HID profile spec: 0x0206 is the descriptor
    itself, 0x0205/0x0204 say we can reconnect and want to be reconnected to,
    and 0x020d/0x020e are the boot-protocol flags. The two protocol descriptor
    lists (0x0004, 0x000d) are what name PSM 17 and 19.
    """
    return f"""<?xml version="1.0" encoding="UTF-8" ?>
<record>
  <attribute id="0x0001"><sequence><uuid value="0x1124" /></sequence></attribute>
  <attribute id="0x0004"><sequence>
    <sequence><uuid value="0x0100" /><uint16 value="0x{P_CTRL:04x}" /></sequence>
    <sequence><uuid value="0x0011" /></sequence>
  </sequence></attribute>
  <attribute id="0x0005"><sequence><uuid value="0x1002" /></sequence></attribute>
  <attribute id="0x0006"><sequence>
    <uint16 value="0x656e" /><uint16 value="0x006a" /><uint16 value="0x0100" />
  </sequence></attribute>
  <attribute id="0x0009"><sequence>
    <sequence><uuid value="0x1124" /><uint16 value="0x0100" /></sequence>
  </sequence></attribute>
  <attribute id="0x000d"><sequence><sequence>
    <sequence><uuid value="0x0100" /><uint16 value="0x{P_INTR:04x}" /></sequence>
    <sequence><uuid value="0x0011" /></sequence>
  </sequence></sequence></attribute>
  <attribute id="0x0100"><text value="{name}" /></attribute>
  <attribute id="0x0101"><text value="Gesture pointer" /></attribute>
  <attribute id="0x0102"><text value="hands-free" /></attribute>
  <attribute id="0x0200"><uint16 value="0x0100" /></attribute>
  <attribute id="0x0201"><uint16 value="0x0111" /></attribute>
  <attribute id="0x0202"><uint8 value="0x40" /></attribute>
  <attribute id="0x0203"><uint8 value="0x00" /></attribute>
  <attribute id="0x0204"><boolean value="true" /></attribute>
  <attribute id="0x0205"><boolean value="true" /></attribute>
  <attribute id="0x0206"><sequence><sequence>
    <uint8 value="0x22" /><text encoding="hex" value="{descriptor.hex()}" />
  </sequence></sequence></attribute>
  <attribute id="0x0207"><sequence><sequence>
    <uint16 value="0x0409" /><uint16 value="0x0100" />
  </sequence></sequence></attribute>
  <attribute id="0x020b"><uint16 value="0x0100" /></attribute>
  <attribute id="0x020c"><uint16 value="0x0c80" /></attribute>
  <attribute id="0x020d"><boolean value="true" /></attribute>
  <attribute id="0x020e"><boolean value="false" /></attribute>
  <attribute id="0x020f"><uint16 value="0x0640" /></attribute>
  <attribute id="0x0210"><uint16 value="0x0320" /></attribute>
</record>
"""


def _dbus():
    """Import D-Bus late, so this module can at least be read on a laptop."""
    import dbus
    import dbus.mainloop.glib
    import dbus.service
    return dbus, dbus.service


def advertise(name="Hands-Free", pointer="relative", verbose=True):
    """Register the agent and the SDP record, and make the Pi discoverable.

    Returns the D-Bus objects, which have to be kept alive — BlueZ drops the
    profile the moment our connection goes away.
    """
    dbus, dbus_service = _dbus()
    import dbus.mainloop.glib
    from gi.repository import GLib

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    class Agent(dbus_service.Object):
        """Accept every pairing request. A headless box can't ask."""

        @dbus_service.method("org.bluez.Agent1", in_signature="", out_signature="")
        def Release(self):
            pass

        @dbus_service.method("org.bluez.Agent1", in_signature="o", out_signature="")
        def RequestAuthorization(self, device):
            _say(f"pairing: authorised {device}")

        @dbus_service.method("org.bluez.Agent1", in_signature="os", out_signature="")
        def AuthorizeService(self, device, uuid):
            _say(f"pairing: authorised service {uuid}")

        @dbus_service.method("org.bluez.Agent1", in_signature="o", out_signature="u")
        def RequestPasskey(self, device):
            return dbus.UInt32(0)

        @dbus_service.method("org.bluez.Agent1", in_signature="o", out_signature="s")
        def RequestPinCode(self, device):
            return "0000"

        @dbus_service.method("org.bluez.Agent1", in_signature="ouq", out_signature="")
        def DisplayPasskey(self, device, passkey, entered):
            _say(f"pairing: passkey {int(passkey):06d}")

        @dbus_service.method("org.bluez.Agent1", in_signature="os", out_signature="")
        def DisplayPinCode(self, device, pincode):
            _say(f"pairing: pin {pincode}")

        @dbus_service.method("org.bluez.Agent1", in_signature="ou", out_signature="")
        def RequestConfirmation(self, device, passkey):
            _say(f"pairing: confirmed {int(passkey):06d}")

        @dbus_service.method("org.bluez.Agent1", in_signature="", out_signature="")
        def Cancel(self):
            _say("pairing: cancelled by the host")

    class Profile(dbus_service.Object):
        """A stub so BlueZ has somewhere to call. We drive L2CAP ourselves."""

        @dbus_service.method("org.bluez.Profile1", in_signature="", out_signature="")
        def Release(self):
            pass

        @dbus_service.method("org.bluez.Profile1", in_signature="oha{sv}",
                             out_signature="")
        def NewConnection(self, path, fd, properties):
            pass

        @dbus_service.method("org.bluez.Profile1", in_signature="o",
                             out_signature="")
        def RequestDisconnection(self, path):
            pass

    def _say(msg):
        if verbose:
            print(f"  {msg}", flush=True)

    props = dbus.Interface(bus.get_object("org.bluez", "/org/bluez/hci0"),
                           "org.freedesktop.DBus.Properties")
    props.Set("org.bluez.Adapter1", "Alias", name)
    props.Set("org.bluez.Adapter1", "Powered", dbus.Boolean(1))
    props.Set("org.bluez.Adapter1", "Pairable", dbus.Boolean(1))
    props.Set("org.bluez.Adapter1", "Discoverable", dbus.Boolean(1))

    agent = Agent(bus, AGENT_PATH)
    manager = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"),
                             "org.bluez.AgentManager1")
    manager.RegisterAgent(AGENT_PATH, "NoInputNoOutput")
    manager.RequestDefaultAgent(AGENT_PATH)

    profile = Profile(bus, PROFILE_PATH)
    pm = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"),
                        "org.bluez.ProfileManager1")
    try:
        pm.UnregisterProfile(PROFILE_PATH)     # a previous run may still own it
    except dbus.DBusException:
        pass
    pm.RegisterProfile(PROFILE_PATH, HID_UUID, {
        "ServiceRecord": service_record(name, hid.combined_descriptor(pointer)),
        "Role": "server",
        "RequireAuthentication": dbus.Boolean(False),
        "RequireAuthorization": dbus.Boolean(False),
        "AutoConnect": dbus.Boolean(True),
    })

    loop = GLib.MainLoop()
    threading.Thread(target=loop.run, daemon=True, name="bluez-dbus").start()

    address = str(props.Get("org.bluez.Adapter1", "Address"))
    klass = int(props.Get("org.bluez.Adapter1", "Class"))
    return {"bus": bus, "agent": agent, "profile": profile, "loop": loop,
            "address": address, "class": klass, "props": props}


class Backend:
    """A paired Mac on the other end of two L2CAP sockets."""

    name = "bluetooth"
    keyboard_id = hid.KEYBOARD_ID
    mouse_id = hid.MOUSE_ID

    def __init__(self, cfg=None, verbose=True):
        cfg = cfg or {}
        self.verbose = verbose
        self._lock = threading.Lock()
        self._ctrl = self._intr = None
        self._peer = None
        self._closed = False

        self.bluez = advertise(cfg.get("name", "Hands-Free"),
                               cfg.get("pointer", "relative"), verbose)

        self._listen_ctrl = self._bind(P_CTRL)
        self._listen_intr = self._bind(P_INTR)

        self._accepting = threading.Thread(target=self._accept_forever,
                                           daemon=True, name="bt-accept")
        self._accepting.start()

    # -- setup -------------------------------------------------------------

    @staticmethod
    def _bind(psm):
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET,
                             socket.BTPROTO_L2CAP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((socket.BDADDR_ANY, psm))
        except PermissionError:
            raise SystemExit(
                f"PSM {psm} is a privileged Bluetooth port — run this with sudo.")
        except OSError as e:
            raise SystemExit(
                f"could not bind L2CAP PSM {psm}: {e}\n"
                "  If this is 'Address already in use', bluetoothd still has "
                "its input\n  plugin loaded and is holding the port. Run "
                "`python3 -m handsfree pair --setup`.")
        sock.listen(1)
        return sock

    def _accept_forever(self):
        """Take the host back whenever it reconnects — a lid close drops it."""
        while not self._closed:
            try:
                ctrl, addr = self._listen_ctrl.accept()
                intr, _ = self._listen_intr.accept()
            except OSError:
                return                      # closed under us; we're shutting down

            with self._lock:
                self._ctrl, self._intr, self._peer = ctrl, intr, addr[0]
            if self.verbose:
                print(f"  host connected: {addr[0]}", flush=True)

            # Whatever state the last session ended in, the host doesn't know
            # about it. Start from a neutral one so a button can't survive a
            # reconnect held down.
            self.send_mouse(hid.mouse_report(report_id=self.mouse_id))
            self.send_keyboard(hid.keyboard_report(report_id=self.keyboard_id))

            self._wait_for_hangup(ctrl)

    def _wait_for_hangup(self, ctrl):
        """Block until the control channel drops, then drop everything."""
        try:
            while not self._closed:
                if not ctrl.recv(64):
                    break
        except OSError:
            pass
        with self._lock:
            self._ctrl = self._intr = self._peer = None
        if self.verbose and not self._closed:
            print("  host disconnected", flush=True)

    # -- the interface -----------------------------------------------------

    @property
    def connected(self):
        return self._intr is not None

    @property
    def peer(self):
        return self._peer

    def send_mouse(self, report):
        self._send(report)

    def send_keyboard(self, report):
        self._send(report)

    def _send(self, report):
        with self._lock:
            intr = self._intr
        if intr is None:
            return                          # nobody listening; not an error
        try:
            intr.send(bytes([HIDP_INPUT]) + bytes(report))
        except OSError:
            # The host vanished mid-report. The accept thread will notice and
            # reset; dropping this one report is the right answer.
            with self._lock:
                self._intr = None

    def wait_for_host(self, timeout=None):
        """Block until a host connects. Returns True if one did."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.connected:
            if deadline is not None and time.monotonic() > deadline:
                return False
            time.sleep(0.1)
        return True

    def close(self):
        self._closed = True
        with self._lock:
            socks = [self._ctrl, self._intr, self._listen_ctrl,
                     self._listen_intr]
            self._ctrl = self._intr = None
        for sock in socks:
            try:
                sock.close()
            except OSError:
                pass
        try:
            import dbus
            pm = dbus.Interface(
                self.bluez["bus"].get_object("org.bluez", "/org/bluez"),
                "org.bluez.ProfileManager1")
            pm.UnregisterProfile(PROFILE_PATH)
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
