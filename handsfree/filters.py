"""One Euro filter — smoothing that doesn't add lag when you move fast.

Landmarks jitter by ~1-2 per-mille of the frame even with a perfectly still
hand, which is enough to make a cursor crawl. A plain low-pass fixes the crawl
and adds lag; One Euro varies its cutoff with speed, so it's heavy when you're
still and nearly transparent when you're moving.

Timestamps are passed in explicitly rather than assuming a fixed rate: the
frame interval here wanders between 100 and 150 ms, and a fixed-rate version
would mis-scale every time MediaPipe stalls.

After Casiez, Roussel & Vogel (2012).
"""

import math


def _alpha(cutoff, dt):
    tau = 1.0 / (2 * math.pi * cutoff)
    return 1.0 / (1.0 + tau / dt)


class _LowPass:
    def __init__(self):
        self.y = None

    def __call__(self, x, alpha):
        self.y = x if self.y is None else alpha * x + (1 - alpha) * self.y
        return self.y


class OneEuro:
    """Filters a single scalar.

    min_cutoff  lower = steadier when still, but laggier
    beta        higher = less lag when moving fast, but jumpier
    """

    def __init__(self, min_cutoff=0.5, beta=5.0, d_cutoff=1.0):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self.x = _LowPass()
        self.dx = _LowPass()
        self.prev = None
        self.t_prev = None

    def __call__(self, x, t):
        if self.t_prev is None or t <= self.t_prev:
            self.t_prev, self.prev = t, x
            return self.x(x, 1.0)

        dt = t - self.t_prev
        self.t_prev = t

        rate = (x - self.prev) / dt
        self.prev = x
        edx = self.dx(rate, _alpha(self.d_cutoff, dt))

        cutoff = self.min_cutoff + self.beta * abs(edx)
        return self.x(x, _alpha(cutoff, dt))

    def reset(self):
        self.__init__(self.min_cutoff, self.beta, self.d_cutoff)


class OneEuro2D:
    """Two OneEuros, for a point. Each axis is filtered independently."""

    def __init__(self, min_cutoff=0.5, beta=5.0, d_cutoff=1.0):
        self.fx = OneEuro(min_cutoff, beta, d_cutoff)
        self.fy = OneEuro(min_cutoff, beta, d_cutoff)

    def __call__(self, x, y, t):
        return self.fx(x, t), self.fy(y, t)

    def reset(self):
        self.fx.reset()
        self.fy.reset()
