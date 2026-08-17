"""Tests for the layer that actually moves the Mac's cursor.

The failure this file exists to prevent is a **mouse button left held down**.
Everything upstream of here has failure modes measured in annoyance; this one
has a failure mode measured in "reboot the laptop". So most of what follows is
the same question asked four ways: after this, is the button up?

The rest checks the interpolation thread does what it's for — that the deltas
it emits add up to the travel it was asked for, that slow movement isn't
rounded away to nothing, and that one bad frame can't fling the cursor.

Runs on a laptop with no hardware, against `transport.null`.

    python3 test_driver.py
"""

import glob
import os
import signal

from handsfree import hid
from handsfree.driver import Driver
from handsfree.gestures import Event
from handsfree.transport.null import Backend as Null

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


class Clock:
    """A hand-cranked clock, so nothing here has to sleep."""

    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt
        return self.t


def build(clock=None, **cfg):
    """A driver wired to a silent null transport, with the thread off."""
    clock = clock or Clock()
    transport = Null(quiet=True)
    driver = Driver(transport, cfg, clock=clock, thread=False)
    return driver, transport, clock


def buttons(transport):
    """The button byte of every mouse report sent, in order."""
    return [r[0] for kind, r in transport.sent if kind == "mouse"]


def travel(transport, axis=0):
    """Total signed movement across every mouse report."""
    lo = 1 + axis * 2
    return sum(int.from_bytes(r[lo:lo + 2], "little", signed=True)
               for kind, r in transport.sent if kind == "mouse")


def run(driver, clock, seconds, hz=120.0):
    for _ in range(int(seconds * hz)):
        clock.advance(1.0 / hz)
        driver.pump()


# -- the button is never left down ------------------------------------------

@test
def test_close_releases_a_held_button():
    driver, transport, _ = build()
    driver.handle([Event("click_down")])
    assert driver.buttons == 1
    driver.close()
    assert driver.buttons == 0
    assert buttons(transport)[-1] == 0, "last report should be button-up"


@test
def test_close_is_idempotent():
    """It's called from `finally` and from the signal handler, both."""
    driver, transport, _ = build()
    driver.handle([Event("click_down")])
    driver.close()
    n = len(transport.sent)
    driver.close()
    driver.close()
    assert len(transport.sent) == n, "closing twice shouldn't send twice"


@test
def test_the_watchdog_releases_when_the_vision_loop_stops_feeding_us():
    """A hung MediaPipe must not leave the button down."""
    driver, transport, clock = build(watchdog=0.5, max_drag=0)
    driver.handle([Event("click_down")])
    run(driver, clock, 0.3)
    assert driver.buttons == 1, "half a watchdog shouldn't trip it"
    run(driver, clock, 0.5)
    assert driver.buttons == 0
    assert driver.released_by == "watchdog"
    assert buttons(transport)[-1] == 0


@test
def test_the_ceiling_releases_even_while_the_engine_insists_on_dragging():
    """recordings/drag.jsonl has a thumb that never fully lets go. Down here
    that would be a stuck button, so there's a hard limit."""
    driver, transport, clock = build(max_drag=2.0, watchdog=0)
    driver.handle([Event("click_down")])
    for _ in range(300):                # keep feeding, so the watchdog can't fire
        clock.advance(1 / 120)
        driver.handle([Event("cursor", (0.5, 0.5))])
        driver.pump()
    assert driver.buttons == 0
    assert driver.released_by == "max_drag"


@test
def test_a_dropped_transport_clears_the_held_button():
    driver, transport, clock = build()
    driver.handle([Event("click_down")])

    class Dropped(Null):
        connected = False

    driver.transport = Dropped(quiet=True)
    run(driver, clock, 0.05)
    assert driver.buttons == 0
    assert driver.released_by == "disconnect"


@test
def test_sigterm_mid_drag_releases_the_button():
    driver, transport, _ = build()
    driver.install_signal_guards()
    driver.handle([Event("click_down")])
    try:
        signal.raise_signal(signal.SIGTERM)
    except SystemExit:
        pass
    else:
        raise AssertionError("the guard should still let the process exit")
    assert driver.buttons == 0
    assert buttons(transport)[-1] == 0


@test
def test_hand_lost_releases_even_without_a_click_up():
    """Belt and braces — the engine emits click_up first, but if it ever
    stopped doing that this is the case that ruins a Mac."""
    driver, transport, _ = build()
    driver.handle([Event("click_down")])
    driver.handle([Event("hand_lost")])
    assert driver.buttons == 0
    assert buttons(transport)[-1] == 0


def drive(path, **cfg):
    """Push a whole recording through engine and driver, on the clip's clock.

    Primes the same clips `replay.py` primes, so this exercises the engine
    exactly as the tuning loop does rather than a slightly different machine.
    """
    from handsfree.cli.replay import PRIMED, load
    from handsfree.gestures import GestureEngine

    driver, transport, _ = build(**cfg)
    engine = GestureEngine()
    primed = os.path.basename(path)[:-6] in PRIMED
    t = 0.0
    for _header, t, landmarks in load(path):
        driver.clock = lambda t=t: t
        driver.handle(engine.update(landmarks, t))
        driver.pump()
        if primed and landmarks and engine.state == "PARKED":
            engine.take_control(landmarks, t)
    return driver, transport, t


@test
def test_no_recording_can_hold_the_button_past_the_ceiling():
    """The real thing: every clip we have, driven end to end on real config.

    A clip can legitimately run out mid-pinch — `pinch` does, and so would a
    real session that ended with your hand still closed. That's the tape
    ending, not a stuck button. What must never happen is a hold that outlasts
    `max_drag` with no guard firing.
    """
    paths = sorted(glob.glob("recordings/*.jsonl"))
    if not paths:
        return                          # recordings are gitignored; skip if absent

    for path in paths:
        driver, transport, end = drive(path)
        label = os.path.basename(path)
        if driver.buttons:
            held = end - driver.drag_since
            assert held <= float(driver.cfg["max_drag"]), \
                f"{label} held the button {held:.1f}s with no guard firing"
        driver.close()
        # Whatever happened during the clip, shutting down releases it. This is
        # the property that makes the difference between an annoying bug and a
        # laptop you have to reboot.
        assert driver.buttons == 0, label
        sent = buttons(transport)
        # A clip where nothing happens sends nothing but the neutral report
        # close() emits — `baseline` and `park` are supposed to be silent.
        assert not sent or sent[-1] == 0, f"{label} last report has it down"


@test
def test_the_ceiling_is_what_saves_the_drag_recording():
    """Open problem #3, pinned down.

    `recordings/drag.jsonl` ends with a pinch the gesture layer never sees
    released — the thumb stays at a ratio of ~0.3 instead of returning to 1.5 —
    leaving an 8 second unterminated drag. Upstream that costs a missed drag.
    Here it would be a stuck mouse button, so the ceiling has to be what
    catches it, and it has to catch it by a clear margin rather than by luck.
    """
    path = "recordings/drag.jsonl"
    if not os.path.exists(path):
        return

    driver, transport, _ = drive(path)
    assert driver.buttons == 0
    assert driver.released_by == "max_drag", \
        f"expected the ceiling to catch it, got {driver.released_by!r}"

    # And with every guard switched off, it genuinely does hang — which is why
    # the guard isn't optional. If this ever stops being true the gesture layer
    # has been fixed and open problem #3 can be closed.
    driver, transport, _ = drive(path, watchdog=0, max_drag=0)
    assert driver.buttons == 1, \
        "drag.jsonl no longer hangs — open problem #3 may be fixed"
    driver.close()
    assert buttons(transport)[-1] == 0, "close() must still release it"


# -- movement ----------------------------------------------------------------

@test
def test_the_deltas_add_up_to_the_travel_requested():
    """Interpolation may spread movement over many reports, but it must not
    invent or lose any."""
    driver, transport, clock = build(speed=1000.0, ease=0.02, max_step=1000)
    driver.handle([Event("cursor", (0.9, 0.5))])
    run(driver, clock, 2.0)
    # 0.5 -> 0.9 of a 1000px space is 400px.
    assert abs(travel(transport, 0) - 400) <= 1, travel(transport, 0)
    assert abs(travel(transport, 1)) <= 1, travel(transport, 1)


@test
def test_slow_movement_is_not_rounded_away_to_nothing():
    """Without a carried remainder, a hand creeping across the frame produces
    a sub-pixel delta every tick, truncates to zero, and never moves at all."""
    driver, transport, clock = build(speed=1000.0, ease=0.5)
    driver.handle([Event("cursor", (0.51, 0.5))])
    run(driver, clock, 3.0)
    assert travel(transport, 0) >= 9, \
        f"10px of travel produced {travel(transport, 0)}"


@test
def test_one_bad_frame_cannot_fling_the_cursor():
    driver, transport, clock = build(speed=100000.0, max_step=30, ease=0.0)
    driver.handle([Event("cursor", (1.0, 1.0))])
    run(driver, clock, 0.05)
    steps = [int.from_bytes(r[1:3], "little", signed=True)
             for kind, r in transport.sent if kind == "mouse"]
    assert steps and max(abs(s) for s in steps) <= 30, max(steps)


@test
def test_a_capped_step_is_deferred_not_discarded():
    """The clipped remainder has to come out on later ticks, or a fast sweep
    silently travels less far than the hand did."""
    driver, transport, clock = build(speed=1000.0, max_step=5, ease=0.0)
    driver.handle([Event("cursor", (0.6, 0.5))])   # 100px at 1000px/unit
    run(driver, clock, 1.0)
    assert abs(travel(transport, 0) - 100) <= 1, travel(transport, 0)


@test
def test_the_cursor_settles_and_then_shuts_up():
    """A target that stops moving must stop producing reports, or an idle hand
    trickles jitter into the Mac forever."""
    driver, transport, clock = build(speed=1000.0, ease=0.02)
    driver.handle([Event("cursor", (0.7, 0.3))])
    run(driver, clock, 1.0)
    before = len(transport.sent)
    run(driver, clock, 1.0)
    assert len(transport.sent) == before, \
        f"{len(transport.sent) - before} reports after settling"


@test
def test_nothing_moves_before_the_first_cursor_event():
    """PARKED must be genuinely still, not easing toward a default centre."""
    driver, transport, clock = build()
    run(driver, clock, 1.0)
    assert transport.sent == [], transport.sent


@test
def test_absolute_mode_maps_the_normalized_cursor_onto_the_box():
    driver, transport, clock = build(pointer="absolute", ease=0.0)
    driver.handle([Event("cursor", (1.0, 0.0))])
    run(driver, clock, 0.1)
    kind, last = transport.sent[-1]
    assert int.from_bytes(last[1:3], "little") == hid.ABSOLUTE_MAX, last.hex()
    assert int.from_bytes(last[3:5], "little") == 0, last.hex()


@test
def test_absolute_mode_repeats_nothing():
    driver, transport, clock = build(pointer="absolute", ease=0.0)
    driver.handle([Event("cursor", (0.4, 0.6))])
    run(driver, clock, 0.5)
    assert len(transport.sent) == 1, \
        f"a still hand sent {len(transport.sent)} absolute reports"


@test
def test_an_unknown_pointer_mode_is_refused_at_construction():
    try:
        build(pointer="trackball")
    except ValueError:
        return
    raise AssertionError("a typo in config.toml should fail loudly and early")


# -- scroll and swipe --------------------------------------------------------

@test
def test_scroll_ticks_reach_the_wheel_byte():
    driver, transport, _ = build()
    driver.handle([Event("scroll", 3)])
    wheels = [int.from_bytes(r[5:6], "little", signed=True)
              for kind, r in transport.sent if kind == "mouse"]
    assert sum(wheels) == 3, wheels


@test
def test_a_huge_scroll_is_split_rather_than_clamped():
    """The wheel byte tops out at 127. Losing the rest would make a big scroll
    silently shorter than a small one."""
    driver, transport, _ = build()
    driver.handle([Event("scroll", 300)])
    wheels = [int.from_bytes(r[5:6], "little", signed=True)
              for kind, r in transport.sent if kind == "mouse"]
    assert sum(wheels) == 300, wheels
    assert all(abs(w) <= 127 for w in wheels), wheels


@test
def test_scroll_direction_is_configurable():
    driver, transport, _ = build(scroll=-1.0)
    driver.handle([Event("scroll", 5)])
    wheels = [int.from_bytes(r[5:6], "little", signed=True)
              for kind, r in transport.sent if kind == "mouse"]
    assert sum(wheels) == -5, wheels


@test
def test_a_swipe_presses_and_then_releases_its_shortcut():
    """A key report with no matching release repeats until something stops it,
    which on a Mac means switching spaces forever."""
    driver, transport, _ = build()
    driver.handle([Event("swipe", "left")])
    keys = [r for kind, r in transport.sent if kind == "keys"]
    assert len(keys) == 2, keys
    assert keys[0][0] == 0x01 and keys[0][2] == 0x50, keys[0].hex()
    assert keys[1] == b"\x00" * 8, keys[1].hex()


@test
def test_swipe_shortcuts_come_from_config():
    driver, transport, _ = build(swipe_right="cmd+shift+]")
    driver.handle([Event("swipe", "right")])
    keys = [r for kind, r in transport.sent if kind == "keys"]
    assert keys[0][0] == (0x08 | 0x02), keys[0].hex()
    assert keys[0][2] == hid.KEYS["]"], keys[0].hex()


# -- report shape ------------------------------------------------------------

@test
def test_report_ids_follow_the_transport_not_the_driver():
    """Bluetooth shares one descriptor and needs IDs; USB doesn't have them.
    Getting this backwards makes the host ignore every report, silently."""
    driver, transport, _ = build()
    transport.mouse_id, transport.keyboard_id = hid.MOUSE_ID, hid.KEYBOARD_ID
    driver.handle([Event("click_down"), Event("swipe", "left")])

    for kind, report in transport.sent:
        expected = hid.MOUSE_ID if kind == "mouse" else hid.KEYBOARD_ID
        assert report[0] == expected, (kind, report.hex())
        assert len(report) == (8 if kind == "mouse" else 9), report.hex()


@test
def test_movement_while_dragging_keeps_the_button_down():
    driver, transport, clock = build(speed=1000.0, ease=0.0)
    driver.handle([Event("click_down"), Event("cursor", (0.8, 0.5))])
    run(driver, clock, 0.2)
    assert all(b == 1 for b in buttons(transport)), buttons(transport)
    assert travel(transport, 0) > 0


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
