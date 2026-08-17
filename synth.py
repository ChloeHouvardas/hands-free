"""Build hand landmarks from scratch, without a camera or a hand.

The recordings are one person's hands on one afternoon in one room. Tuning
only against them risks thresholds that fit that afternoon and nothing else.
So this generates hands to order — a known pose, at a known place, at a known
angle, with as much noise as you want — and the tests drive the gesture engine
with them.

It's a real forward-kinematic hand, not a fake: each finger is a chain of three
bones whose joints bend by however much you asked, so `finger_angles` reads
back exactly the curl that went in. That's what makes it useful for testing —
the ground truth is something you set, not something you inferred.

    python synth.py            # draw the poses to synth.png, to eyeball them
"""

import math
import random
from types import SimpleNamespace

# Canonical hand, palm at the origin, fingers pointing up the -y axis, sized so
# that wrist-to-middle-MCP (the hand_scale everything is measured in) is 1.0.
MCP = {
    "index":  (-0.34, -0.92),
    "middle": (0.00, -1.00),
    "ring":   (0.31, -0.93),
    "pinky":  (0.58, -0.80),
}
# Bone lengths: proximal, middle, distal.
BONES = {
    "index":  (0.42, 0.26, 0.21),
    "middle": (0.46, 0.28, 0.22),
    "ring":   (0.42, 0.26, 0.21),
    "pinky":  (0.32, 0.21, 0.19),
}
ORDER = ["index", "middle", "ring", "pinky"]

# A fully curled finger bends this far at each joint. Straight is 0.
MAX_BEND = 110.0


def _chain(start, direction, lengths, bend):
    """Walk a bone chain, turning by `bend` degrees at every joint.

    Bending at each joint by the same amount is what makes the PIP and DIP
    angles come out equal to 180 - bend, which is exactly the number
    `finger_angles` measures.
    """
    pts = []
    x, y = start
    theta = direction
    for i, L in enumerate(lengths):
        if i:
            theta += math.radians(bend)
        x += L * math.cos(theta)
        y += L * math.sin(theta)
        pts.append((x, y))
    return pts


def hand(x=0.5, y=0.5, scale=0.22, rotation=0.0, curl=None, thumb=45.0,
         pinch=None, noise=0.0, rng=None):
    """21 landmarks for one hand.

    x, y       palm centre, in normalized frame coords
    scale      wrist-to-middle-knuckle length, same units
    rotation   degrees clockwise; the whole point of angle-based features is
               that this shouldn't change a single decision
    curl       per-finger 0.0 (straight) to 1.0 (fully closed)
    thumb      abduction in degrees — how far the thumb swings off the palm
    pinch      if set, put the thumb tip this many hand-sizes from the index
               tip, which is the ratio the pinch detector measures
    noise      per-landmark jitter, in hand-sizes
    """
    rng = rng or random
    curl = dict(curl or {})
    pts = {0: (0.0, 0.0)}   # wrist

    for name in ORDER:
        mx, my = MCP[name]
        c = max(0.0, min(1.0, curl.get(name, 0.0)))
        # Fingers splay slightly, and each one points away from the wrist.
        direction = math.atan2(my, mx) * 0.35 + math.radians(-90) * 0.65
        chain = _chain((mx, my), direction, BONES[name], c * MAX_BEND)
        base = {"index": 5, "middle": 9, "ring": 13, "pinky": 17}[name]
        pts[base] = (mx, my)
        for i, p in enumerate(chain):
            pts[base + 1 + i] = p

    # Thumb. Placed by where its tip needs to end up, then the two middle
    # joints are spread along the way — a real thumb rotates at the base
    # rather than curling, so the finger model doesn't apply to it.
    if pinch is not None:
        ix, iy = pts[8]
        # Approach the index tip from the palm side, so the geometry stays
        # plausible however tight the pinch is.
        ang = math.atan2(0.0 - iy, 0.0 - ix)
        tip = (ix + pinch * math.cos(ang), iy + pinch * math.sin(ang))
    else:
        # thumb_abduction is the angle at the wrist between the thumb tip and
        # the index knuckle, so aim the tip that many degrees off that line.
        ix, iy = MCP["index"]
        base_ang = math.atan2(iy, ix)
        ang = base_ang - math.radians(thumb)
        tip = (1.05 * math.cos(ang), 1.05 * math.sin(ang))

    for i, f in enumerate((0.25, 0.55, 0.80, 1.0), start=1):
        pts[i] = (tip[0] * f, tip[1] * f)

    out = []
    cos, sin = math.cos(math.radians(rotation)), math.sin(math.radians(rotation))
    for i in range(21):
        px, py = pts[i]
        if noise:
            px += rng.gauss(0, noise)
            py += rng.gauss(0, noise)
        rx, ry = px * cos - py * sin, px * sin + py * cos
        out.append(SimpleNamespace(x=x + rx * scale, y=y + ry * scale, z=0.0))
    return out


# -- named poses, matching what the gesture engine looks for -----------------

OPEN = {}
POINT = {"middle": 0.9, "ring": 0.95, "pinky": 0.9}
LAZY_POINT = {"middle": 0.75, "ring": 0.6, "pinky": 0.15}   # pinky tags along
TWO = {"ring": 0.95, "pinky": 0.9}
THREE = {"pinky": 0.9}
FIST = {"index": 1.0, "middle": 1.0, "ring": 1.0, "pinky": 1.0}

POSES = {
    "OPEN": (OPEN, 50.0),
    "POINT": (POINT, 45.0),
    "LAZY_POINT": (LAZY_POINT, 45.0),
    "TWO": (TWO, 30.0),
    "THREE": (THREE, 25.0),      # thumb tucked, which is what separates it
    "FIST": (FIST, 20.0),
}


def pose(name, **kw):
    curl, thumb = POSES[name]
    kw.setdefault("thumb", thumb)
    return hand(curl=curl, **kw)


def clip(frames, fps=9.5, t0=0.0):
    """Attach timestamps to a list of landmark lists, as a recording would."""
    return [(t0 + i / fps, lm) for i, lm in enumerate(frames)]


def hold(name, seconds, fps=9.5, **kw):
    """The same pose, still, for a while."""
    return [pose(name, **kw) for _ in range(max(1, int(seconds * fps)))]


def sweep(name, seconds, x0, x1, y0=0.5, y1=None, fps=9.5, **kw):
    """A pose travelling in a straight line."""
    y1 = y0 if y1 is None else y1
    n = max(2, int(seconds * fps))
    out = []
    for i in range(n):
        f = i / (n - 1)
        out.append(pose(name, x=x0 + (x1 - x0) * f, y=y0 + (y1 - y0) * f, **kw))
    return out


def pinching(seconds, ratio=0.2, fps=9.5, curl=None, **kw):
    """A pinch held closed. Ring curled, as a real pinch is."""
    curl = curl if curl is not None else {"ring": 0.9, "pinky": 0.9}
    n = max(1, int(seconds * fps))
    return [hand(curl=curl, pinch=ratio, **kw) for _ in range(n)]


def main():
    """Draw the poses so they can be eyeballed rather than trusted."""
    import cv2
    import numpy as np

    from hand import CONNECTIONS

    names = list(POSES)
    canvas = np.zeros((260, 240 * len(names), 3), np.uint8)
    for col, name in enumerate(names):
        lm = pose(name, x=0.5, y=0.62, scale=0.28)
        pts = [(int((p.x + col) * 240), int(p.y * 260)) for p in lm]
        for a, b in CONNECTIONS:
            cv2.line(canvas, pts[a], pts[b], (0, 220, 0), 2)
        for p in pts:
            cv2.circle(canvas, p, 3, (0, 0, 255), -1)
        cv2.putText(canvas, name, (col * 240 + 10, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    cv2.imwrite("synth.png", canvas)
    print("wrote synth.png")


if __name__ == "__main__":
    main()
