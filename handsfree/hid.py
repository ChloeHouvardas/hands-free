"""HID report descriptors and report bytes. No hardware, no imports.

This is the byte-level half of the transport, kept separate from anything that
opens a socket or a device node so it can be tested on a laptop — same reason
`hand.py` and `filters.py` are the shape they are. Every descriptor here is
checked against a decoder in `test_hid.py`, because a descriptor that is wrong
by one byte doesn't error, it just makes the host ignore you.

Two shapes of the same thing, because the two transports want different ones:

* **Bluetooth** publishes one descriptor in its SDP record, so the keyboard and
  the mouse have to share it and be told apart by a leading report ID.
* **USB gadget** gets a ConfigFS function per device, `/dev/hidg0` and
  `/dev/hidg1`, each with its own descriptor and no report ID at all.

So every builder here takes `report_id=None` and quietly leaves the byte out.

The pointer comes in two flavours, and which one is right isn't obvious:

* `relative` is what every mouse does — deltas, universally understood. macOS
  then applies its own pointer acceleration on top, which means hand position
  and cursor position drift apart over a session and the One Euro filter ends
  up fighting a second, invisible filter.
* `absolute` reports a position in a 0..32767 box, the way a graphics tablet
  does. No acceleration, no drift, and the engine already keeps its cursor in
  normalized screen space, so it's very nearly a straight multiply.

Both are the same seven bytes on the wire; only the descriptor differs. That's
why this is a config switch rather than an argument to have up front.
"""

KEYBOARD_ID = 1
MOUSE_ID = 2

#: Both pointer modes ride the same report ID, because only one of them is in
#: the descriptor at a time. See combined_descriptor for why.
POINTER_IDS = {"relative": MOUSE_ID, "absolute": MOUSE_ID}

# Report descriptor items are (prefix, data...) where the low two bits of the
# prefix are the data length. Spelling them out as bytes rather than building
# them from a DSL keeps them diffable against the HID spec tables.

_KEYBOARD = [
    0x05, 0x01,        # Usage Page (Generic Desktop)
    0x09, 0x06,        # Usage (Keyboard)
    0xA1, 0x01,        # Collection (Application)
    None,              # report ID slot
    0x05, 0x07,        #   Usage Page (Keyboard/Keypad)
    0x19, 0xE0,        #   Usage Minimum (Left Control)
    0x29, 0xE7,        #   Usage Maximum (Right GUI)
    0x15, 0x00,        #   Logical Minimum (0)
    0x25, 0x01,        #   Logical Maximum (1)
    0x75, 0x01,        #   Report Size (1)
    0x95, 0x08,        #   Report Count (8)
    0x81, 0x02,        #   Input (Data, Var, Abs)  -- the modifier byte
    0x95, 0x01,        #   Report Count (1)
    0x75, 0x08,        #   Report Size (8)
    0x81, 0x01,        #   Input (Const)           -- reserved byte, always 0
    0x95, 0x06,        #   Report Count (6)
    0x75, 0x08,        #   Report Size (8)
    0x15, 0x00,        #   Logical Minimum (0)
    0x25, 0x65,        #   Logical Maximum (101)
    0x05, 0x07,        #   Usage Page (Keyboard/Keypad)
    0x19, 0x00,        #   Usage Minimum (0)
    0x29, 0x65,        #   Usage Maximum (101)
    0x81, 0x00,        #   Input (Data, Array)     -- six simultaneous keys
    0xC0,              # End Collection
]

# Everything up to the X/Y axes is identical between the two pointer modes.
_MOUSE_HEAD = [
    0x05, 0x01,        # Usage Page (Generic Desktop)
    0x09, 0x02,        # Usage (Mouse)
    0xA1, 0x01,        # Collection (Application)
    None,              # report ID slot
    0x09, 0x01,        #   Usage (Pointer)
    0xA1, 0x00,        #   Collection (Physical)
    0x05, 0x09,        #     Usage Page (Button)
    0x19, 0x01,        #     Usage Minimum (Button 1)
    0x29, 0x05,        #     Usage Maximum (Button 5)
    0x15, 0x00,        #     Logical Minimum (0)
    0x25, 0x01,        #     Logical Maximum (1)
    0x95, 0x05,        #     Report Count (5)
    0x75, 0x01,        #     Report Size (1)
    0x81, 0x02,        #     Input (Data, Var, Abs)
    0x95, 0x01,        #     Report Count (1)
    0x75, 0x03,        #     Report Size (3)
    0x81, 0x01,        #     Input (Const)          -- pad the button byte out
    0x05, 0x01,        #     Usage Page (Generic Desktop)
    0x09, 0x30,        #     Usage (X)
    0x09, 0x31,        #     Usage (Y)
]

# 16-bit axes, not the usual 8. A vision loop at ~9 fps sets a new target every
# ~110 ms, and an 8-bit axis caps a single report at 127 units — which a fast
# hand sweep blows straight through. The interpolation thread means we rarely
# send a big delta, but the headroom costs one byte per axis.
_MOUSE_RELATIVE_AXES = [
    0x16, 0x01, 0x80,  #     Logical Minimum (-32767)
    0x26, 0xFF, 0x7F,  #     Logical Maximum (32767)
    0x75, 0x10,        #     Report Size (16)
    0x95, 0x02,        #     Report Count (2)
    0x81, 0x06,        #     Input (Data, Var, Rel)
]

_MOUSE_ABSOLUTE_AXES = [
    0x15, 0x00,        #     Logical Minimum (0)
    0x26, 0xFF, 0x7F,  #     Logical Maximum (32767)
    0x75, 0x10,        #     Report Size (16)
    0x95, 0x02,        #     Report Count (2)
    0x81, 0x02,        #     Input (Data, Var, Abs)
]

# Wheel is vertical, AC Pan is horizontal. Both stay relative in absolute mode —
# there is no such thing as an absolute scroll position. Expect vertical scroll
# to feel notchy: macOS quantizes wheel input to lines and does not expose the
# momentum a real trackpad gets.
_MOUSE_TAIL = [
    0x09, 0x38,        #     Usage (Wheel)
    0x15, 0x81,        #     Logical Minimum (-127)
    0x25, 0x7F,        #     Logical Maximum (127)
    0x75, 0x08,        #     Report Size (8)
    0x95, 0x01,        #     Report Count (1)
    0x81, 0x06,        #     Input (Data, Var, Rel)
    0x05, 0x0C,        #     Usage Page (Consumer)
    0x0A, 0x38, 0x02,  #     Usage (AC Pan)
    0x15, 0x81,        #     Logical Minimum (-127)
    0x25, 0x7F,        #     Logical Maximum (127)
    0x75, 0x08,        #     Report Size (8)
    0x95, 0x01,        #     Report Count (1)
    0x81, 0x06,        #     Input (Data, Var, Rel)
    0xC0,              #   End Collection
    0xC0,              # End Collection
]

POINTERS = ("relative", "absolute")

#: Widest value an absolute axis can carry, from Logical Maximum above.
ABSOLUTE_MAX = 0x7FFF


def _fill(items, report_id):
    """Drop the report ID into its slot, or remove the slot entirely."""
    out = []
    for item in items:
        if item is None:
            if report_id is not None:
                out += [0x85, report_id]    # Report ID (n)
        else:
            out.append(item)
    return bytes(out)


def keyboard_descriptor(report_id=None):
    return _fill(_KEYBOARD, report_id)


def mouse_descriptor(report_id=None, pointer="relative"):
    if pointer not in POINTERS:
        raise ValueError(f"pointer must be one of {POINTERS}, not {pointer!r}")
    axes = _MOUSE_ABSOLUTE_AXES if pointer == "absolute" else _MOUSE_RELATIVE_AXES
    return _fill(_MOUSE_HEAD + axes + _MOUSE_TAIL, report_id)


def combined_descriptor(pointer="relative"):
    """One keyboard and **one** pointer, told apart by report ID.

    What Bluetooth publishes in its SDP record.

    A previous version carried both pointer modes at once, on separate report
    IDs, so that switching `[hid] pointer` wouldn't need a re-pair — macOS reads
    this once, at pair time, and caches it. That was tidier and it did not work:
    macOS stopped acting on reports entirely, while the same bytes still parsed
    correctly under the Linux kernel's HID parser. The difference is two
    `Usage(Mouse)` application collections in one descriptor; IOHIDFamily is
    stricter than Linux about matching collections to drivers, and quietly
    binds nothing rather than complaining.

    So: one pointer per descriptor, chosen here. Switching mode means forgetting
    the device on the Mac and pairing again. Worse ergonomics, but it works,
    and the alternative silently doesn't.
    """
    return (keyboard_descriptor(KEYBOARD_ID)
            + mouse_descriptor(MOUSE_ID, pointer))


# -- reports ---------------------------------------------------------------

def keyboard_report(modifiers=0, keys=(), report_id=None):
    """8 bytes: modifiers, a reserved zero, and up to six simultaneous keys."""
    keys = list(keys)
    if len(keys) > 6:
        raise ValueError("a keyboard report holds at most six keys")
    body = bytes([modifiers & 0xFF, 0x00]) + bytes(keys) + bytes(6 - len(keys))
    return (bytes([report_id]) if report_id is not None else b"") + body


def mouse_report(buttons=0, x=0, y=0, wheel=0, pan=0, report_id=None,
                 pointer="relative"):
    """7 bytes: buttons, two 16-bit axes, wheel, AC Pan.

    In relative mode `x`/`y` are signed deltas; in absolute mode they are
    unsigned positions in 0..32767. The layout is identical either way, which
    is what lets the driver switch modes without knowing anything else.
    """
    if pointer == "absolute":
        x = _clamp(x, 0, ABSOLUTE_MAX)
        y = _clamp(y, 0, ABSOLUTE_MAX)
        axes = x.to_bytes(2, "little") + y.to_bytes(2, "little")
    else:
        x = _clamp(x, -ABSOLUTE_MAX, ABSOLUTE_MAX)
        y = _clamp(y, -ABSOLUTE_MAX, ABSOLUTE_MAX)
        axes = (x.to_bytes(2, "little", signed=True)
                + y.to_bytes(2, "little", signed=True))

    body = (bytes([buttons & 0x1F]) + axes
            + _signed_byte(wheel) + _signed_byte(pan))
    return (bytes([report_id]) if report_id is not None else b"") + body


def _clamp(v, lo, hi):
    v = int(v)
    return lo if v < lo else (hi if v > hi else v)


def _signed_byte(v):
    return _clamp(v, -127, 127).to_bytes(1, "little", signed=True)


# -- keys ------------------------------------------------------------------

#: Modifier bits, in the order the modifier byte packs them.
MODIFIERS = {
    "ctrl": 0x01, "shift": 0x02, "alt": 0x04, "cmd": 0x08,
    "rctrl": 0x10, "rshift": 0x20, "ralt": 0x40, "rcmd": 0x80,
}
# macOS names them differently from the HID spec, and the HID spec names them
# differently from what anyone types. Accept all of it.
MODIFIERS.update({
    "control": 0x01, "option": 0x04, "opt": 0x04, "meta": 0x08,
    "gui": 0x08, "super": 0x08, "win": 0x08,
})

#: HID usage IDs from the Keyboard/Keypad page. Only what the config needs.
KEYS = {
    "left": 0x50, "right": 0x4F, "down": 0x51, "up": 0x52,
    "tab": 0x2B, "space": 0x2C, "enter": 0x28, "return": 0x28,
    "esc": 0x29, "escape": 0x29, "backspace": 0x2A, "delete": 0x4C,
    "home": 0x4A, "end": 0x4D, "pageup": 0x4B, "pagedown": 0x4E,
    "[": 0x2F, "]": 0x30, "-": 0x2D, "=": 0x2E, "`": 0x35,
}
for _i, _c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    KEYS[_c] = 0x04 + _i
for _i in range(1, 10):
    KEYS[str(_i)] = 0x1D + _i           # 1..9 are 0x1E..0x26
KEYS["0"] = 0x27
for _i in range(1, 13):
    KEYS[f"f{_i}"] = 0x39 + _i          # F1..F12 are 0x3A..0x45
del _i, _c


def combo(spec):
    """Parse `"ctrl+left"` into `(modifier_bits, keycode)`.

    Used for the swipe shortcuts. macOS multi-touch gestures cannot be sent by
    any external device, so a three-finger swipe becomes a keyboard shortcut —
    `Ctrl+Left`/`Ctrl+Right` for spaces by default. Worth checking those are
    actually enabled in the target Mac's Keyboard Shortcuts before blaming this.
    """
    parts = [p.strip().lower() for p in str(spec).split("+") if p.strip()]
    if not parts:
        raise ValueError("empty key combo")

    # No modifier name collides with a key name — every modifier is a word and
    # every single-character key is a character — so the order doesn't matter.
    modifiers, key = 0, None
    for part in parts:
        if part in MODIFIERS:
            modifiers |= MODIFIERS[part]
        elif part in KEYS:
            if key is not None:
                raise ValueError(f"{spec!r} names more than one key")
            key = KEYS[part]
        else:
            raise ValueError(f"unknown key {part!r} in {spec!r}")

    if key is None:
        raise ValueError(f"{spec!r} is all modifiers and no key")
    return modifiers, key
