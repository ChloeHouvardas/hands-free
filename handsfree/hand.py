"""Pure hand geometry — no camera, no MediaPipe, no OpenCV.

Everything here works on a plain list of 21 points with .x/.y/.z, whether they
came from a live camera or a recorded JSONL file. That's deliberate: it's what
lets the gesture layer be tuned on a laptop with no hardware attached.

Everything is also rotation- and scale-invariant, which matters because the
camera is mounted sideways and your hand is never at a fixed distance. Joint
angles don't care about either; raw coordinates care about both.
"""

import math

# Landmark indices worth naming.
WRIST = 0
THUMB_MCP = 2
THUMB_IP = 3
THUMB_TIP = 4
INDEX_MCP = 5
INDEX_TIP = 8
MIDDLE_MCP = 9
MIDDLE_TIP = 12
RING_MCP = 13
PINKY_MCP = 17
PINKY_TIP = 20

# (mcp, pip, dip, tip) for each of the four fingers. The thumb isn't here —
# it bends in a different plane and needs its own test.
FINGERS = {
    "index":  (5, 6, 7, 8),
    "middle": (9, 10, 11, 12),
    "ring":   (13, 14, 15, 16),
    "pinky":  (17, 18, 19, 20),
}

# (start, end) pairs describing the hand skeleton, for drawing.
CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),           # thumb
    (0, 5), (5, 6), (6, 7), (7, 8),           # index
    (5, 9), (9, 10), (10, 11), (11, 12),      # middle
    (9, 13), (13, 14), (14, 15), (15, 16),    # ring
    (13, 17), (17, 18), (18, 19), (19, 20),   # pinky
    (0, 17),                                  # palm
]

# The points that make up the palm. Their mean is far steadier than any
# fingertip, because it doesn't move when fingers flex.
PALM = (WRIST, INDEX_MCP, MIDDLE_MCP, RING_MCP, PINKY_MCP)


def distance(a, b):
    return math.hypot(a.x - b.x, a.y - b.y)


def angle(a, b, c):
    """Interior angle at b, in degrees. 180 is straight."""
    v1x, v1y = a.x - b.x, a.y - b.y
    v2x, v2y = c.x - b.x, c.y - b.y
    n1 = math.hypot(v1x, v1y)
    n2 = math.hypot(v2x, v2y)
    if n1 == 0 or n2 == 0:
        return 180.0
    cos = (v1x * v2x + v1y * v2y) / (n1 * n2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def hand_scale(landmarks):
    """Wrist to middle-finger MCP.

    Every distance threshold in the gesture layer is a multiple of this, so
    moving your hand nearer or further from the camera doesn't change how the
    gestures behave.
    """
    return distance(landmarks[WRIST], landmarks[MIDDLE_MCP])


def palm_centroid(landmarks):
    """Mean of the palm landmarks. This is what we track for motion gestures."""
    pts = [landmarks[i] for i in PALM]
    return (sum(p.x for p in pts) / len(pts),
            sum(p.y for p in pts) / len(pts))


def finger_angles(landmarks):
    """How straight each finger is, in degrees. Name -> angle.

    The obvious test for "is this finger extended" compares the tip's y against
    the knuckle's y, which is just wrong the moment the hand tilts — and this
    camera is mounted sideways, so the hand is always tilted. A joint angle is
    intrinsic to the finger and doesn't care which way up anything is.

    Taking the min of the PIP and DIP angles catches the half-curls where the
    knuckle is straight but the tip is hooked over.
    """
    out = {}
    for name, (mcp, pip, dip, tip) in FINGERS.items():
        out[name] = min(
            angle(landmarks[mcp], landmarks[pip], landmarks[dip]),
            angle(landmarks[pip], landmarks[dip], landmarks[tip]),
        )
    return out


def pinch_ratio(landmarks):
    """Thumb tip to index tip, over hand size.

    Measured on real recordings: a pinched hand sits around 0.2, an open one
    around 1.5. The gap is wide, which is why pinch is the most reliable thing
    in the whole vocabulary.
    """
    scale = hand_scale(landmarks)
    if scale == 0:
        return float("inf")
    return distance(landmarks[THUMB_TIP], landmarks[INDEX_TIP]) / scale


def thumb_abduction(landmarks):
    """How far the thumb is swung away from the index knuckle, in degrees.

    The thumb rotates at its base rather than curling, so the finger angle test
    doesn't transfer. Tucked into the palm reads under 20, spread reads near 50.
    """
    return angle(landmarks[THUMB_TIP], landmarks[WRIST], landmarks[INDEX_MCP])
