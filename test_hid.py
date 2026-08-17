"""Tests for the HID byte layer.

A wrong report descriptor doesn't raise anything. The host just quietly ignores
you, or moves the cursor the wrong distance, and you spend an evening blaming
Bluetooth. So rather than freeze the bytes in a golden file — which only tells
you they changed, not whether they're right — these tests parse the descriptor
back with a small decoder and check the thing that actually matters: that the
report it *describes* is the same size and shape as the report we send.

    python3 test_hid.py
"""

from handsfree import hid

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


# -- a real, if minimal, HID report descriptor decoder ----------------------

# Item prefixes we care about, by (tag, type) packed the way the spec does.
_GLOBAL_REPORT_SIZE = 0x74
_GLOBAL_REPORT_COUNT = 0x94
_GLOBAL_REPORT_ID = 0x84
_MAIN_INPUT = 0x80
_MAIN_COLLECTION = 0xA0
_MAIN_END_COLLECTION = 0xC0

_SIZES = {0: 0, 1: 1, 2: 2, 3: 4}


def decode(desc):
    """Walk a descriptor and total up the input bits behind each report ID.

    Returns `{report_id: bits}`, using `None` as the key when the descriptor
    carries no report IDs at all. Deliberately ignores usages and logical
    ranges — this is here to catch a mis-sized report, which is the failure
    that costs the most time.
    """
    sizes, counts, report_id = 0, 0, None
    bits = {}
    depth = 0
    i = 0
    while i < len(desc):
        prefix = desc[i]
        length = _SIZES[prefix & 0x03]
        data = int.from_bytes(desc[i + 1:i + 1 + length], "little")
        tag = prefix & 0xFC

        if tag == _GLOBAL_REPORT_SIZE:
            sizes = data
        elif tag == _GLOBAL_REPORT_COUNT:
            counts = data
        elif tag == _GLOBAL_REPORT_ID:
            report_id = data
        elif tag == _MAIN_INPUT:
            bits[report_id] = bits.get(report_id, 0) + sizes * counts
        elif tag == _MAIN_COLLECTION:
            depth += 1
        elif tag == _MAIN_END_COLLECTION:
            depth -= 1
            if depth < 0:
                raise AssertionError(f"unbalanced End Collection at byte {i}")

        i += 1 + length

    if i != len(desc):
        raise AssertionError(f"descriptor overruns its own length: {i} != "
                             f"{len(desc)}")
    if depth != 0:
        raise AssertionError(f"{depth} collection(s) left open")
    return bits


# -- descriptors -------------------------------------------------------------

@test
def test_every_descriptor_is_well_formed():
    """Balanced collections, no item running off the end."""
    for pointer in hid.POINTERS:
        decode(hid.mouse_descriptor(pointer=pointer))
        decode(hid.mouse_descriptor(hid.MOUSE_ID, pointer=pointer))
        decode(hid.combined_descriptor(pointer))
    decode(hid.keyboard_descriptor())
    decode(hid.keyboard_descriptor(hid.KEYBOARD_ID))


@test
def test_the_descriptor_describes_the_report_we_actually_send():
    """The one that matters. Bits promised == bytes sent, for every variant."""
    for pointer in hid.POINTERS:
        bits = decode(hid.mouse_descriptor(pointer=pointer))
        report = hid.mouse_report(pointer=pointer)
        assert bits[None] == len(report) * 8, \
            f"{pointer} mouse: descriptor says {bits[None]} bits, " \
            f"report is {len(report) * 8}"

    bits = decode(hid.keyboard_descriptor())
    report = hid.keyboard_report()
    assert bits[None] == len(report) * 8, \
        f"keyboard: descriptor says {bits[None]} bits, report is " \
        f"{len(report) * 8}"


@test
def test_the_combined_descriptor_sizes_both_reports():
    """Bluetooth publishes one descriptor for both devices."""
    for pointer in hid.POINTERS:
        bits = decode(hid.combined_descriptor(pointer))
        assert set(bits) == {hid.KEYBOARD_ID, hid.MOUSE_ID}, sorted(bits)

        # The report ID byte is part of the report but not counted in the
        # descriptor's input bits, hence the -1.
        kbd = hid.keyboard_report(report_id=hid.KEYBOARD_ID)
        mouse = hid.mouse_report(report_id=hid.MOUSE_ID, pointer=pointer)
        assert bits[hid.KEYBOARD_ID] == (len(kbd) - 1) * 8, bits
        assert bits[hid.MOUSE_ID] == (len(mouse) - 1) * 8, bits


@test
def test_a_report_id_is_the_only_difference_between_the_two_shapes():
    """USB gets one function per device and no report IDs; Bluetooth shares."""
    plain = hid.keyboard_descriptor()
    tagged = hid.keyboard_descriptor(hid.KEYBOARD_ID)
    assert len(tagged) == len(plain) + 2, (len(plain), len(tagged))
    assert bytes([0x85, hid.KEYBOARD_ID]) in tagged
    assert 0x85 not in plain, "a descriptor with no report id shouldn't have one"


@test
def test_relative_and_absolute_differ_only_in_their_axes():
    rel = hid.mouse_descriptor(pointer="relative")
    absolute = hid.mouse_descriptor(pointer="absolute")
    assert rel != absolute
    # Same report size either way, which is what lets the driver switch modes
    # without anything downstream noticing.
    assert len(hid.mouse_report(pointer="relative")) == \
        len(hid.mouse_report(pointer="absolute"))


@test
def test_an_unknown_pointer_mode_is_refused_loudly():
    try:
        hid.mouse_descriptor(pointer="joystick")
    except ValueError:
        return
    raise AssertionError("a typo in config.toml should not silently pass")


# -- reports -----------------------------------------------------------------

@test
def test_a_resting_mouse_report_is_all_zeros():
    assert hid.mouse_report() == b"\x00" * 7, hid.mouse_report().hex()
    assert hid.keyboard_report() == b"\x00" * 8, hid.keyboard_report().hex()


@test
def test_negative_deltas_are_two_s_complement_little_endian():
    r = hid.mouse_report(x=-1, y=-300)
    assert r[1:3] == b"\xff\xff", r.hex()
    assert r[3:5] == (-300).to_bytes(2, "little", signed=True), r.hex()


@test
def test_axes_are_clamped_rather_than_wrapping():
    """A landmark glitch has to not become a cursor teleport in the wrong
    direction, which is what an overflowing to_bytes would produce."""
    r = hid.mouse_report(x=999999, y=-999999)
    assert int.from_bytes(r[1:3], "little", signed=True) == hid.ABSOLUTE_MAX
    assert int.from_bytes(r[3:5], "little", signed=True) == -hid.ABSOLUTE_MAX

    r = hid.mouse_report(wheel=900, pan=-900)
    assert r[5] == 127, r.hex()
    assert int.from_bytes(r[6:7], "little", signed=True) == -127, r.hex()


@test
def test_absolute_axes_are_unsigned_and_clamped_to_the_box():
    r = hid.mouse_report(x=-5, y=hid.ABSOLUTE_MAX + 5, pointer="absolute")
    assert int.from_bytes(r[1:3], "little") == 0, r.hex()
    assert int.from_bytes(r[3:5], "little") == hid.ABSOLUTE_MAX, r.hex()


@test
def test_buttons_land_in_the_low_five_bits():
    assert hid.mouse_report(buttons=1)[0] == 0x01
    assert hid.mouse_report(buttons=0xFF)[0] == 0x1F, "only five buttons exist"


@test
def test_a_report_id_prefixes_the_report():
    assert hid.mouse_report(report_id=hid.MOUSE_ID)[0] == hid.MOUSE_ID
    assert hid.keyboard_report(report_id=hid.KEYBOARD_ID)[0] == hid.KEYBOARD_ID
    assert len(hid.mouse_report(report_id=2)) == len(hid.mouse_report()) + 1


@test
def test_keyboard_reports_pad_out_to_six_keys():
    r = hid.keyboard_report(modifiers=0x01, keys=[0x50])
    assert len(r) == 8, r.hex()
    assert r[0] == 0x01 and r[1] == 0x00 and r[2] == 0x50
    assert r[3:] == b"\x00" * 5, r.hex()


@test
def test_a_seventh_key_is_refused():
    try:
        hid.keyboard_report(keys=[4] * 7)
    except ValueError:
        return
    raise AssertionError("HID boot keyboards hold six keys, not seven")


# -- key combos --------------------------------------------------------------

@test
def test_the_default_swipe_shortcuts_parse():
    assert hid.combo("ctrl+left") == (0x01, 0x50)
    assert hid.combo("ctrl+right") == (0x01, 0x4F)


@test
def test_combos_accept_the_names_people_actually_type():
    assert hid.combo("cmd+shift+]") == (0x08 | 0x02, 0x30)
    assert hid.combo("CTRL+Left") == (0x01, 0x50)
    assert hid.combo("control+left") == hid.combo("ctrl+left")
    assert hid.combo("opt+tab") == hid.combo("option+tab") == (0x04, 0x2B)
    assert hid.combo("left") == (0x00, 0x50)


@test
def test_a_bad_combo_names_itself_in_the_error():
    for bad in ("ctrl+", "ctrl+shift", "ctrl+nope", "", "a+b"):
        try:
            hid.combo(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} should not have parsed")


@test
def test_the_key_table_is_internally_consistent():
    assert hid.KEYS["a"] == 0x04 and hid.KEYS["z"] == 0x1D
    assert hid.KEYS["1"] == 0x1E and hid.KEYS["9"] == 0x26
    assert hid.KEYS["0"] == 0x27
    assert hid.KEYS["f1"] == 0x3A and hid.KEYS["f12"] == 0x45
    assert not set(hid.KEYS) & set(hid.MODIFIERS), \
        "a name meaning both a key and a modifier would make combo() ambiguous"


def main():
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
