"""Tests for the gesture layer, on hands built by synth.py.

These exist because the recordings can only tell us the thresholds work on the
hands we happened to record. These check the *logic*: that a pinch is one click
and not two, that the button can never be left held down, that a gesture means
the same thing at 8 fps and 30 fps, and that none of it depends on which way up
the camera is.

    python test_gestures.py
"""

import math
import random

import synth
from gestures import GestureEngine, load_config


def run(frames, fps=9.5, engine=None):
    """Feed frames to an engine. Returns (events, engine).

    `frames` is a list of landmark lists, or None for a frame where the hand
    wasn't detected.
    """
    engine = engine or GestureEngine()
    events = []
    for i, lm in enumerate(frames):
        for e in engine.update(lm, i / fps):
            events.append((e.name, e.value))
    return events, engine


def names(events, *skip):
    skip = set(skip) | {"cursor", "hand_found"}
    return [n for n, _ in events if n not in skip]


def count(events, name):
    return sum(1 for n, _ in events if n == name)


def wake(name, seconds=0.9, **kw):
    """Frames that take control and settle into `name`.

    Pointing is the only way out of PARKED, so every sequence starts by
    pointing — which is what live use looks like too. Then the pose itself has
    to be held a moment before the engine commits to it.
    """
    frames = synth.hold("POINT", 0.9, **kw)
    if name != "POINT":
        frames += synth.hold(name, seconds, **kw)
    else:
        frames += synth.hold("POINT", max(0.0, seconds - 0.9), **kw)
    return frames


def _thumb_out(lm, cfg):
    from hand import thumb_abduction
    return thumb_abduction(lm) > cfg["fingers"]["thumb_out"]


TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


# -- poses -------------------------------------------------------------------

@test
def test_poses_classify_at_any_rotation():
    """The whole reason for using joint angles instead of coordinates."""
    from gestures import Fingers, classify
    cfg = load_config()
    want = {"OPEN": "PALM", "POINT": "POINT", "LAZY_POINT": "POINT",
            "TWO": "TWO", "THREE": "THREE"}
    for name, expect in want.items():
        for rot in (0, 45, 90, 180, 270, 337):
            lm = synth.pose(name, rotation=rot)
            f = Fingers(cfg["fingers"])
            for _ in range(3):
                state = f.update(lm)
            out = classify(state, _thumb_out(lm, cfg))
            assert out == expect, f"{name} at {rot}deg read as {out}"


@test
def test_pose_survives_distance_and_position():
    """Thresholds are in hand-sizes, so none of this should matter."""
    from gestures import Fingers, classify
    cfg = load_config()
    for scale in (0.10, 0.22, 0.40):
        for x, y in ((0.2, 0.2), (0.5, 0.5), (0.85, 0.8)):
            lm = synth.pose("TWO", x=x, y=y, scale=scale)
            f = Fingers(cfg["fingers"])
            for _ in range(3):
                state = f.update(lm)
            out = classify(state, _thumb_out(lm, cfg))
            assert out == "TWO", f"TWO at scale {scale} ({x},{y}) read as {out}"


# -- nothing should happen ---------------------------------------------------

@test
def test_open_palm_does_nothing():
    events, engine = run(synth.hold("OPEN", 5))
    assert names(events) == [], events
    assert engine.state == "PARKED"


@test
def test_still_hand_does_nothing():
    for pose_name in ("OPEN", "FIST", "POINT", "TWO", "THREE"):
        events, _ = run(synth.hold(pose_name, 4))
        assert count(events, "click_down") == 0, pose_name
        assert count(events, "scroll") == 0, pose_name
        assert count(events, "swipe") == 0, pose_name


@test
def test_only_pointing_takes_control():
    """The rule the whole false-positive story rests on.

    Two or three fingers held out for a long time is what an idle hand looks
    like, not a request to scroll the page.
    """
    for name in ("TWO", "THREE", "FIST", "OPEN"):
        _, engine = run(synth.hold(name, 5.0))
        assert engine.state == "PARKED", f"{name} took control ({engine.state})"

    _, engine = run(synth.hold("POINT", 2.0))
    assert engine.state == "MOVE", engine.state


@test
def test_taking_control_needs_more_than_a_flicker():
    """A frame or two of accidental pointing shouldn't wake anything."""
    frames = (synth.hold("OPEN", 1.0) + synth.hold("POINT", 0.2)
              + synth.hold("OPEN", 1.0))
    _, engine = run(frames)
    assert engine.state == "PARKED", engine.state


@test
def test_scroll_and_swipe_are_reachable_once_pointing():
    """The flip side: after pointing, the modes still work."""
    _, engine = run(wake("TWO"))
    assert engine.state == "SCROLL", engine.state
    _, engine = run(wake("THREE"))
    assert engine.state == "SWIPE", engine.state


@test
def test_opening_your_palm_always_parks():
    """The escape hatch has to work from every state, and from a sloppy palm.

    Reported live: opening the hand was read as a three-finger swipe, or as a
    pinch, so there was no way to stop. Parking is how you get out of anything,
    so it has to win whenever it's ambiguous.
    """
    for start in ("POINT", "TWO", "THREE"):
        frames = wake(start) + synth.hold("OPEN", 1.5)
        events, engine = run(frames)
        assert engine.state == "PARKED", \
            f"from {start}: ended in {engine.state}"
        assert count(events, "swipe") == 0, (start, names(events))

    # And from a drag, where the button also has to come back up.
    frames = wake("POINT") + synth.pinching(0.5) + synth.hold("OPEN", 1.5)
    events, engine = run(frames)
    assert engine.state == "PARKED", engine.state
    assert count(events, "click_up") == 1, names(events)


@test
def test_a_lazy_open_palm_still_parks():
    """Fingers not fully straight — still a park.

    Either a reasonably straight pinky or a thumb clear of the palm is enough.
    A bent pinky *and* a tucked thumb is genuinely closer to the swipe pose
    than to an open hand, so that corner is left out on purpose.
    """
    for pinky, thumb in ((0.0, 15.0), (0.0, 30.0), (0.0, 50.0),
                         (0.1, 15.0), (0.1, 50.0), (0.2, 50.0)):
        if True:
            frames = wake("POINT") + [
                synth.hand(curl={"pinky": pinky, "ring": 0.1}, thumb=thumb)
                for _ in range(16)]
            _, engine = run(frames)
            assert engine.state == "PARKED", \
                f"pinky={pinky} thumb={thumb}: {engine.state}"


@test
def test_the_thumb_only_breaks_ties():
    """The pinky decides palm vs swipe; the thumb only votes when it can't.

    A deliberate swipe keeps the pinky folded, and that has to stay a swipe
    even if the thumb happens to drift out — otherwise swipes get eaten. But a
    half-straight pinky is a genuine coin flip, and there PALM wins.
    """
    from gestures import Fingers, classify
    cfg = load_config()

    def read(pinky_curl, thumb):
        lm = synth.hand(curl={"pinky": pinky_curl}, thumb=thumb)
        f = Fingers(cfg["fingers"])
        for _ in range(3):
            state = f.update(lm)
        return classify(state, _thumb_out(lm, cfg), f.ambiguous("pinky"))

    assert read(0.9, 50.0) == "THREE", "folded pinky must stay a swipe"
    assert read(0.9, 10.0) == "THREE"
    assert read(0.0, 10.0) == "PALM", "straight pinky is a palm regardless"
    assert read(0.2, 50.0) == "PALM", "ambiguous pinky + spread thumb = palm"
    assert read(0.2, 10.0) == "THREE", "ambiguous pinky + tucked thumb = swipe"


@test
def test_parked_hand_ignores_a_resting_thumb():
    """A hand resting palm-open with the thumb near the index isn't a click.

    This is the failure the control recording was full of.
    """
    frames = [synth.hand(curl={}, pinch=0.3) for _ in range(40)]
    events, _ = run(frames)
    assert count(events, "click_down") == 0, events


@test
def test_three_fingers_out_is_never_a_pinch():
    """Ring finger extended means the thumb is just resting there."""
    frames = [synth.hand(curl={"pinky": 0.9}, pinch=0.15) for _ in range(40)]
    events, _ = run(frames)
    assert count(events, "click_down") == 0, events


# -- cursor ------------------------------------------------------------------

@test
def test_pointing_moves_the_cursor():
    frames = wake("POINT") + synth.sweep("POINT", 2.0, 0.35, 0.65)
    events, engine = run(frames)
    assert engine.state == "MOVE"
    assert count(events, "cursor") > 10
    assert names(events) == [], names(events)
    assert engine.cursor[0] > 0.6, engine.cursor


@test
def test_lazy_pointing_still_moves_the_cursor():
    """Pinky drifting out is how people actually point."""
    frames = wake("LAZY_POINT") + synth.sweep("LAZY_POINT", 2.0, 0.35, 0.65)
    events, engine = run(frames)
    assert engine.state == "MOVE", engine.state
    assert engine.cursor[0] > 0.6, engine.cursor


@test
def test_parking_is_a_clutch():
    """Park, move your hand elsewhere, point again: the cursor stays put."""
    frames = wake("POINT") + synth.sweep("POINT", 2.0, 0.3, 0.6)
    events, engine = run(frames)
    parked = list(engine.cursor)

    # Open palm, wander to the other side of the frame, point again.
    more = (synth.hold("OPEN", 1.0, x=0.6) + synth.hold("OPEN", 1.0, x=0.2)
            + synth.hold("POINT", 1.0, x=0.2))
    for i, lm in enumerate(more):
        engine.update(lm, 100 + i / 9.5)

    assert engine.state == "MOVE", engine.state
    assert abs(engine.cursor[0] - parked[0]) < 0.05, (parked, engine.cursor)


# -- clicking ----------------------------------------------------------------

@test
def test_one_pinch_is_one_click():
    frames = (wake("POINT")
              + synth.pinching(0.8)
              + synth.hold("POINT", 1.0))
    events, engine = run(frames)
    assert count(events, "click_down") == 1, events
    assert count(events, "click_up") == 1, events
    assert names(events) == ["click_down", "click_up"], names(events)
    assert engine.state != "DRAG"


@test
def test_repeated_pinches_are_separate_clicks():
    frames = wake("POINT")
    for _ in range(4):
        frames += synth.pinching(0.5) + synth.hold("POINT", 0.6)
    events, _ = run(frames)
    assert count(events, "click_down") == 4, events
    assert count(events, "click_up") == 4, events


@test
def test_pinch_hold_and_move_is_a_drag():
    frames = (wake("POINT")
              + synth.pinching(0.3, x=0.35)
              + [synth.hand(curl={"ring": 0.9, "pinky": 0.9}, pinch=0.2,
                            x=0.35 + 0.30 * i / 14) for i in range(15)]
              + synth.hold("POINT", 1.0, x=0.65))
    events, engine = run(frames)
    assert count(events, "click_down") == 1, names(events)
    assert count(events, "click_up") == 1, names(events)
    # The button goes down before the movement and up after it.
    order = names(events)
    assert order == ["click_down", "click_up"], order
    assert engine.cursor[0] > 0.6, engine.cursor


@test
def test_hand_vanishing_mid_pinch_releases_the_button():
    """The failure that would otherwise leave a Mac unusable."""
    frames = wake("POINT") + synth.pinching(0.8) + [None] * 12
    events, engine = run(frames)
    assert count(events, "click_down") == 1, names(events)
    assert count(events, "click_up") == 1, names(events)
    assert names(events)[-2:] == ["click_up", "hand_lost"], names(events)
    assert engine.state == "NO_HAND"


@test
def test_a_dropped_frame_does_not_release_a_drag():
    """MediaPipe drops frames constantly; a drag has to ride through them."""
    frames = wake("POINT") + synth.pinching(0.4) + [None] \
        + synth.pinching(0.4) + [None] + synth.pinching(0.4) \
        + synth.hold("POINT", 1.0)
    events, _ = run(frames)
    assert count(events, "click_down") == 1, names(events)
    assert count(events, "click_up") == 1, names(events)


@test
def test_no_path_leaves_the_button_down():
    """Random poses for a long time, then hand gone. Must end released."""
    rng = random.Random(7)
    for seed in range(12):
        rng = random.Random(seed)
        frames = []
        for _ in range(60):
            if rng.random() < 0.12:
                frames.append(None)
                continue
            if rng.random() < 0.3:
                frames.append(synth.hand(curl={"ring": rng.random(),
                                               "pinky": rng.random()},
                                         pinch=rng.uniform(0.05, 1.6),
                                         x=rng.uniform(0.3, 0.7),
                                         y=rng.uniform(0.3, 0.7)))
            else:
                frames.append(synth.pose(rng.choice(list(synth.POSES)),
                                         x=rng.uniform(0.3, 0.7),
                                         y=rng.uniform(0.3, 0.7),
                                         rotation=rng.uniform(0, 360)))
        frames += [None] * 12
        events, engine = run(frames)
        assert count(events, "click_down") == count(events, "click_up"), \
            f"seed {seed}: {names(events)}"
        assert engine.state == "NO_HAND"


# -- scroll ------------------------------------------------------------------

@test
def test_two_fingers_moving_up_scrolls_up():
    frames = wake("TWO", y=0.6) + synth.sweep("TWO", 2.0, 0.5, 0.5,
                                              y0=0.6, y1=0.3)
    events, engine = run(frames)
    ticks = [v for n, v in events if n == "scroll"]
    assert ticks, names(events)
    assert all(v > 0 for v in ticks), ticks
    assert engine.state == "SCROLL"


@test
def test_two_fingers_moving_down_scrolls_down():
    frames = wake("TWO", y=0.3) + synth.sweep("TWO", 2.0, 0.5, 0.5,
                                              y0=0.3, y1=0.6)
    events, _ = run(frames)
    ticks = [v for n, v in events if n == "scroll"]
    assert ticks, names(events)
    assert all(v < 0 for v in ticks), ticks


@test
def test_scroll_stops_when_the_hand_returns_to_centre():
    """Rate control: the offset sets the speed, so coming back to the middle
    means stopped — not 'scroll back to where you started'."""
    settle = wake("TWO", 1.2, y=0.5)
    out = synth.sweep("TWO", 1.0, 0.5, 0.5, y0=0.5, y1=0.3)
    back = synth.sweep("TWO", 1.0, 0.5, 0.5, y0=0.3, y1=0.5)
    rest = synth.hold("TWO", 2.5, y=0.5)

    engine = GestureEngine()
    fps = 9.5
    ticks = []
    for i, lm in enumerate(settle + out + back + rest):
        t = i / fps
        for e in engine.update(lm, t):
            if e.name == "scroll":
                ticks.append((t, e.value))

    assert ticks, "expected some scrolling on the way out"
    assert all(v > 0 for _, v in ticks), ticks
    centred_at = len(settle + out + back) / fps
    late = [x for x in ticks if x[0] > centred_at + 0.3]
    assert not late, f"still scrolling after returning to centre: {late}"


@test
def test_cursor_movement_does_not_scroll():
    """Sweeping the cursor about must not be mistaken for a scroll."""
    frames = wake("POINT") + synth.sweep("POINT", 2.5, 0.3, 0.7,
                                         y0=0.3, y1=0.7)
    events, _ = run(frames)
    assert count(events, "scroll") == 0, names(events)


# -- swipe -------------------------------------------------------------------

@test
def test_three_fingers_sweeping_right_swipes_right():
    frames = wake("THREE", 0.9, x=0.3) + synth.sweep("THREE", 0.6, 0.3, 0.75)
    events, _ = run(frames)
    dirs = [v for n, v in events if n == "swipe"]
    assert dirs == ["right"], (dirs, names(events))


@test
def test_three_fingers_sweeping_left_swipes_left():
    frames = wake("THREE", 0.9, x=0.75) + synth.sweep("THREE", 0.6, 0.75, 0.3)
    events, _ = run(frames)
    dirs = [v for n, v in events if n == "swipe"]
    assert dirs == ["left"], (dirs, names(events))


@test
def test_one_flick_is_one_swipe():
    """Refractory: a single travel must not fire twice as it continues."""
    frames = wake("THREE", 0.9, x=0.2) + synth.sweep("THREE", 1.0, 0.2, 0.9)
    events, _ = run(frames)
    assert count(events, "swipe") == 1, [e for e in events if e[0] == "swipe"]


@test
def test_slow_drift_is_not_a_swipe():
    """Six seconds to cross the frame is repositioning, not a gesture."""
    frames = wake("THREE", 0.9, x=0.2) + synth.sweep("THREE", 6.0, 0.2, 0.85)
    events, _ = run(frames)
    assert count(events, "swipe") == 0, names(events)


@test
def test_vertical_movement_is_not_a_swipe():
    frames = wake("THREE", 0.9, y=0.25) + synth.sweep(
        "THREE", 0.8, 0.5, 0.5, y0=0.25, y1=0.75)
    events, _ = run(frames)
    assert count(events, "swipe") == 0, names(events)


@test
def test_back_and_forth_swipes_both_register():
    frames = (wake("THREE", 0.9, x=0.25)
              + synth.sweep("THREE", 0.6, 0.25, 0.75)
              + synth.hold("THREE", 0.8, x=0.75)
              + synth.sweep("THREE", 0.6, 0.75, 0.25))
    events, _ = run(frames)
    dirs = [v for n, v in events if n == "swipe"]
    assert dirs == ["right", "left"], dirs


# -- robustness --------------------------------------------------------------

@test
def test_same_events_at_any_frame_rate():
    """Everything is time-gated, so the frame rate shouldn't change meaning.

    This is the check that caught a frame-counting bug once already.
    """
    def build(fps):
        return (synth.hold("POINT", 1.0, fps=fps)
                + synth.pinching(0.6, fps=fps)
                + synth.hold("POINT", 0.8, fps=fps)
                + synth.hold("TWO", 1.2, fps=fps, y=0.6)
                + synth.sweep("TWO", 1.5, 0.5, 0.5, y0=0.6, y1=0.3, fps=fps)
                + synth.hold("THREE", 1.0, fps=fps, x=0.3)
                + synth.sweep("THREE", 0.6, 0.3, 0.75, fps=fps))

    discrete, total = None, None
    for fps in (7, 9.5, 15, 24, 30):
        events, _ = run(build(fps), fps=fps)
        # Discrete events have to match exactly — a click is a click.
        got = [n for n in names(events) if n != "scroll"]
        # Scroll is an integrator, so the tick count lands within rounding of
        # the same distance rather than exactly on it.
        ticks = sum(v for n, v in events if n == "scroll")
        if discrete is None:
            discrete, total = got, ticks
        assert got == discrete, f"{fps}fps gave {got}, expected {discrete}"
        assert abs(ticks - total) <= max(1, 0.2 * abs(total)), \
            f"{fps}fps scrolled {ticks} ticks, expected about {total}"


@test
def test_landmark_noise_does_not_invent_gestures():
    """Jitter well past what the recordings show must stay silent."""
    for seed in range(6):
        rng = random.Random(seed)
        frames = [synth.pose("OPEN", noise=0.05, rng=rng) for _ in range(50)]
        events, _ = run(frames)
        assert names(events) == [], (seed, names(events))


@test
def test_gestures_survive_landmark_noise():
    for seed in range(6):
        rng = random.Random(seed)
        frames = ([synth.pose("POINT", noise=0.03, rng=rng) for _ in range(10)]
                  + [synth.hand(curl={"ring": 0.9, "pinky": 0.9}, pinch=0.18,
                                noise=0.03, rng=rng) for _ in range(6)]
                  + [synth.pose("POINT", noise=0.03, rng=rng)
                     for _ in range(10)])
        events, _ = run(frames)
        assert count(events, "click_down") == 1, (seed, names(events))
        assert count(events, "click_up") == 1, (seed, names(events))


@test
def test_gestures_work_with_the_camera_sideways():
    """The camera is physically mounted at 90 degrees; software rotates the
    frame, but nothing here should care if that's ever wrong."""
    for rot in (0, 90, 180, 270):
        frames = wake("POINT", rotation=rot) + synth.pinching(
            0.6, rotation=rot) + synth.hold("POINT", 0.8, rotation=rot)
        events, _ = run(frames)
        assert count(events, "click_down") == 1, (rot, names(events))
        assert count(events, "click_up") == 1, (rot, names(events))


@test
def test_cursor_never_leaves_the_screen():
    frames = wake("POINT", x=0.5) + synth.sweep("POINT", 3.0, 0.5, 0.98) \
        + synth.sweep("POINT", 3.0, 0.98, 0.02)
    _, engine = run(frames)
    assert 0.0 <= engine.cursor[0] <= 1.0, engine.cursor
    assert 0.0 <= engine.cursor[1] <= 1.0, engine.cursor


def main():
    failed = []
    for fn in TESTS:
        try:
            fn()
            print(f"  pass  {fn.__name__}")
        except AssertionError as e:
            failed.append(fn.__name__)
            print(f"  FAIL  {fn.__name__}\n          {e}")
    print(f"\n{len(TESTS) - len(failed)}/{len(TESTS)} pass")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
