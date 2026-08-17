"""Where input reports go. One tiny interface, three backends.

    gestures -> driver -> Transport -> the Mac

The interface is deliberately dumb — a transport takes finished bytes and puts
them somewhere. All the thinking about what to send lives in `driver.py`, and
all the thinking about what the bytes mean lives in `hid.py`. That split is
what lets both of those be tested on a laptop.

    bluetooth   L2CAP on PSM 17/19, paired as a HID device. Needs no cable.
    usb         /dev/hidg0 and /dev/hidg1 via a ConfigFS gadget. Needs one.
    null        prints reports as hex. Runs anywhere, including the Mac.

The two real backends disagree about report IDs, which is why `keyboard_id` and
`mouse_id` are part of the interface. Bluetooth publishes a single descriptor
in its SDP record, so the two devices share one report stream and are told
apart by a leading ID byte. USB gets a ConfigFS *function* each, with its own
descriptor and its own device node, so an ID would be redundant — and hosts
reject a report whose size doesn't match its descriptor, so getting this wrong
fails silently rather than loudly.

Backends are imported only when asked for. `bluetooth` needs python3-dbus and
`usb` needs a configured gadget, neither of which exists on a laptop, and
`replay --drive` has to keep working there.
"""

BACKENDS = ("bluetooth", "usb", "null")


class Transport:
    """Base class, and the whole of the contract."""

    #: Report ID to prefix each report with, or None to send it bare.
    keyboard_id = None
    mouse_id = None

    #: Whether a host is actually listening. Backends that can't tell say True.
    connected = True

    def send_mouse(self, report):
        raise NotImplementedError

    def send_keyboard(self, report):
        raise NotImplementedError

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def open(name, cfg=None, **kwargs):
    """Build a backend by name. Imported lazily — see the module docstring."""
    if name not in BACKENDS:
        raise ValueError(f"unknown transport {name!r}, "
                         f"expected one of {', '.join(BACKENDS)}")

    import importlib
    module = importlib.import_module(f"handsfree.transport.{name}")
    return module.Backend(cfg or {}, **kwargs)
