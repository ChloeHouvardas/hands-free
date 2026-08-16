"""Pure hand geometry — no camera, no MediaPipe, no OpenCV.

Everything here works on a plain list of 21 points with .x/.y/.z, whether they
came from a live camera or a recorded JSONL file. That's deliberate: it's what
lets the gesture layer be tuned on a laptop with no hardware attached.
"""

import math

# Landmark indices worth naming.
WRIST = 0
THUMB_TIP = 4
INDEX_MCP = 5
INDEX_TIP = 8
MIDDLE_MCP = 9
MIDDLE_TIP = 12
RING_MCP = 13
PINKY_MCP = 17
PINKY_TIP = 20

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


def hand_scale(landmarks):
    """Wrist to middle-finger MCP.

    Every threshold in the gesture layer is expressed as a multiple of this, so
    moving your hand nearer or further from the camera doesn't change how the
    gestures behave.
    """
    return distance(landmarks[WRIST], landmarks[MIDDLE_MCP])


def palm_centroid(landmarks):
    """Mean of the palm landmarks. This is what we track for motion gestures."""
    pts = [landmarks[i] for i in PALM]
    return (sum(p.x for p in pts) / len(pts),
            sum(p.y for p in pts) / len(pts))
