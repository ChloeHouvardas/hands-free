"""Scripted sessions: what a hand does, and what should happen when it does.

Every other test in this project checks a *mechanism* — is this pose named
right, is this byte in the right place, does this guard fire. Nothing checks
the thing a person would actually notice: point and the cursor follows, open
your palm and it stops, pinch once and you get one click and not two.

A script is that description. It pairs a synthetic hand session with what the
session is supposed to produce, so the same object can be used two ways:

    python3 -m handsfree run --source synth:drag    drive the real Mac with it
    python3 test_scenarios.py                       assert the outcome offline

Which means the thing you demo and the thing you assert are the same thing, and
a scenario can't drift away from its own expectation.

Stdlib only — `synth.py` builds the hands and imports nothing hardware.

Note `lead()`: pointing is the only way out of PARKED, and it has to be held
`[pose] wake` seconds (0.45) before it takes. Every script that expects to do
anything at all has to start with it, exactly as a person has to.
"""

from handsfree import synth

FPS = 9.5


def lead(seconds=1.2, **kw):
    """Take control, the way a person does: point, and hold it."""
    return synth.hold("POINT", seconds, fps=FPS, **kw)


def park(seconds=1.5, **kw):
    """Open palm — the clutch. Cursor stops dead."""
    return synth.hold("OPEN", seconds, fps=FPS, **kw)


def _script(fn):
    SCRIPTS[fn.__name__] = fn
    return fn


SCRIPTS = {}


@_script
def move():
    """Point, then travel right. The cursor should follow and then stop."""
    return (lead(x=0.3)
            + synth.sweep("POINT", 2.0, 0.3, 0.7, fps=FPS)
            + synth.hold("POINT", 1.0, x=0.7, fps=FPS)), {
        "doc": "point and move right",
        "cursor_x": "+", "clicks": 0, "swipes": 0, "scroll": 0,
    }


@_script
def click():
    """One deliberate pinch. Exactly one click — not two, not none."""
    return (lead(x=0.5)
            + synth.pinching(0.5, x=0.5, fps=FPS)
            + synth.hold("POINT", 1.5, x=0.5, fps=FPS)), {
        "doc": "point, pinch once, let go",
        "clicks": 1, "swipes": 0, "scroll": 0, "button_at_end": 0,
    }


@_script
def drag():
    """Pinch, travel while held, release. One down, movement, one up."""
    return (lead(x=0.3)
            + synth.pinching(0.4, x=0.3, fps=FPS)
            + synth.dragging(1.5, 0.3, 0.7, fps=FPS)
            + synth.hold("POINT", 1.5, x=0.7, fps=FPS)), {
        "doc": "pinch, drag right, release",
        "clicks": 1, "button_at_end": 0, "moved_while_held": True,
        "cursor_x": "+",
    }


@_script
def clutch():
    """The rule the whole design is built on.

    Point, move, open the palm, move the hand somewhere else, point again. The
    cursor must not follow while parked, and must not jump when control is
    retaken — same as lifting a mouse off the desk and putting it back down.
    """
    return (lead(x=0.3)
            + synth.sweep("POINT", 1.2, 0.3, 0.5, fps=FPS)
            + park(x=0.5)
            + synth.sweep("OPEN", 1.5, 0.5, 0.95, fps=FPS)
            + park(0.5, x=0.95)
            + lead(1.5, x=0.95)
            + synth.hold("POINT", 1.0, x=0.95, fps=FPS)), {
        "doc": "move, park, reposition, point again",
        "clicks": 0, "no_jump_after_park": True,
    }


@_script
def scroll():
    """Two fingers, up then down. Scroll should reverse sign and roughly cancel."""
    return (lead(x=0.5, y=0.5)
            + synth.hold("TWO", 0.8, x=0.5, y=0.5, fps=FPS)
            + synth.sweep("TWO", 2.0, 0.5, 0.5, y0=0.5, y1=0.15, fps=FPS)
            + synth.hold("TWO", 1.0, x=0.5, y=0.15, fps=FPS)
            + synth.sweep("TWO", 2.0, 0.5, 0.5, y0=0.15, y1=0.85, fps=FPS)
            + synth.hold("TWO", 1.0, x=0.5, y=0.85, fps=FPS)), {
        "doc": "two fingers up, then down",
        "scroll_both_ways": True, "clicks": 0,
    }


@_script
def swipe():
    """Three fingers, one sideways sweep. Exactly one swipe, not a stutter."""
    return (lead(x=0.3)
            + synth.hold("THREE", 0.8, x=0.3, fps=FPS)
            + synth.sweep("THREE", 0.5, 0.3, 0.6, fps=FPS)
            + synth.hold("THREE", 1.5, x=0.6, fps=FPS)), {
        "doc": "three-finger sweep right",
        "swipes": 1, "clicks": 0,
    }


@_script
def idle():
    """A hand resting in view, pottering. The control case.

    This is what the entire gesture layer is tuned around, and the only script
    here whose expectation is that **nothing happens at all**. A false click
    while your hand sits on the desk is worse than any missed gesture.
    """
    frames = []
    for x in (0.5, 0.52, 0.48, 0.51, 0.49, 0.5):
        frames += synth.hold("OPEN", 1.0, x=x, fps=FPS)
        frames += synth.hold("TWO", 0.3, x=x, fps=FPS)
        frames += synth.hold("THREE", 0.3, x=x, fps=FPS)
    return frames, {
        "doc": "resting hand, never taking control",
        "clicks": 0, "swipes": 0, "scroll": 0, "silent": True,
    }


@_script
def vanish():
    """Hand yanked out of frame mid-pinch. The stuck-button safety case."""
    return (lead(x=0.5)
            + synth.pinching(0.8, x=0.5, fps=FPS)
            + [None] * 20), {
        "doc": "pinch, then hand disappears",
        "button_at_end": 0, "release_before_lost": True,
    }


@_script
def demo():
    """One of everything, in the order the manual test pass does it.

    This is the script to drive a real Mac with: it exercises every gesture the
    device can produce, in about forty seconds, without needing a hand.
    """
    return (lead(x=0.35)
            + synth.sweep("POINT", 1.5, 0.35, 0.6, fps=FPS)
            + synth.pinching(0.5, x=0.6, fps=FPS)
            + synth.hold("POINT", 1.0, x=0.6, fps=FPS)
            + synth.pinching(0.4, x=0.6, fps=FPS)
            + synth.dragging(1.5, 0.6, 0.35, fps=FPS)
            + synth.hold("POINT", 1.0, x=0.35, fps=FPS)
            + synth.hold("TWO", 0.8, x=0.35, y=0.5, fps=FPS)
            + synth.sweep("TWO", 1.5, 0.35, 0.35, y0=0.5, y1=0.2, fps=FPS)
            + synth.hold("POINT", 1.0, x=0.35, fps=FPS)
            + synth.hold("THREE", 0.8, x=0.3, fps=FPS)
            + synth.sweep("THREE", 0.5, 0.3, 0.6, fps=FPS)
            + synth.hold("THREE", 1.2, x=0.6, fps=FPS)
            + park(2.0, x=0.6)), {
        "doc": "one of everything — move, click, drag, scroll, swipe, park",
        "clicks": 2, "button_at_end": 0,
    }


def names():
    return sorted(SCRIPTS)


def build(name, fps=FPS):
    """A script as `[(t, landmarks)]`, the shape a recording has."""
    return synth.clip(frames(name), fps=fps)


def frames(name):
    if name not in SCRIPTS:
        raise SystemExit(f"unknown script {name!r}; "
                         f"expected one of {', '.join(names())}")
    return SCRIPTS[name]()[0]


def expect(name):
    if name not in SCRIPTS:
        raise SystemExit(f"unknown script {name!r}")
    return SCRIPTS[name]()[1]
