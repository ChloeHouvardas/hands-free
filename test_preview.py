"""Tests for the live-view server, over plain HTTP with no browser.

The preview is the one part of this project with a web surface, and the
tempting way to test it is to drive a real browser. That would be worse. By
spec an `<img>` fed `multipart/x-mixed-replace` fires `load` exactly once,
after the first part — so a browser can tell you one frame arrived and nothing
more, which is strictly less than a socket can tell you. And headless
Chromium's support for multipart images is patchy enough that a red would more
likely mean "Chromium" than "our bug".

So: `urllib`, and read the bytes.

The failure this file exists to prevent is a **viewer that goes away and is
never noticed**. The producer only writes to the socket when a new frame
arrives, so if frames stop, a client that vanished without an RST is never
detected, `clients` never drops back to zero, and `watching` stays true — which
means every subsequent frame gets JPEG-encoded on a 2 GB Pi for nobody at all.
That is the exact opposite of the promise in preview.py's own docstring.

    python3 test_preview.py
"""

import socket
import threading
import time
import urllib.error
import urllib.request

from handsfree.preview import Preview

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


# A real 1x1 JPEG, so the tests don't need OpenCV to produce one. Publishing
# goes straight into the state rather than through update(), which is the only
# part of preview.py that needs cv2.
JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300ffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffc200"
    "0b080001000101011100ffc40014000100000000000000000000000000000009ff"
    "da0008010100000000013fffd9")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    """A Preview on a free port, with a hand-driven frame publisher."""

    def __init__(self):
        self.preview = Preview(port=free_port())
        self.url = f"http://127.0.0.1:{self.preview.port}"

    def publish(self, jpeg=JPEG):
        """What update() does, minus the cv2 encode."""
        state = self.preview.state
        with state.lock:
            state.jpeg = jpeg
            state.seq += 1
            state.changed.notify_all()

    def pump(self, n=5, gap=0.02):
        for _ in range(n):
            self.publish()
            time.sleep(gap)

    def wait_for_clients(self, n, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.preview.state.clients == n:
                return True
            time.sleep(0.02)
        return False

    def close(self):
        self.preview.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def read_parts(resp, want, timeout=5.0):
    """Pull `want` JPEG payloads out of a multipart/x-mixed-replace stream."""
    parts, buf = [], b""
    deadline = time.monotonic() + timeout
    while len(parts) < want and time.monotonic() < deadline:
        chunk = resp.read(1024)
        if not chunk:
            break
        buf += chunk
        while True:
            start = buf.find(b"\xff\xd8")
            end = buf.find(b"\xff\xd9", start + 2) if start >= 0 else -1
            if start < 0 or end < 0:
                break
            parts.append(buf[start:end + 2])
            buf = buf[end + 2:]
    return parts


# -- the page ---------------------------------------------------------------

@test
def test_the_page_is_served():
    with Server() as s:
        with urllib.request.urlopen(s.url, timeout=3) as r:
            body = r.read()
        assert r.status == 200
        assert b"<img" in body and b"/stream" in body, body[:200]


@test
def test_an_unknown_path_is_a_404():
    with Server() as s:
        try:
            urllib.request.urlopen(s.url + "/nope", timeout=3)
        except urllib.error.HTTPError as e:
            assert e.code == 404, e.code
            return
        raise AssertionError("anything but / and /stream should 404")


@test
def test_the_query_string_the_reconnect_js_adds_is_ignored():
    """The page cache-busts with /stream?t=... — if that 404'd, every reconnect
    would fail and the live view would never come back after a restart."""
    with Server() as s:
        with urllib.request.urlopen(f"{s.url}/stream?t=12345", timeout=3) as r:
            assert r.status == 200
            assert "multipart/x-mixed-replace" in r.headers["Content-Type"]


# -- the stream -------------------------------------------------------------

@test
def test_the_stream_declares_the_boundary_it_actually_uses():
    with Server() as s:
        with urllib.request.urlopen(s.url + "/stream", timeout=3) as r:
            assert r.headers["Content-Type"] == \
                "multipart/x-mixed-replace; boundary=FRAME", \
                r.headers["Content-Type"]
            threading.Thread(target=s.pump, daemon=True).start()
            raw = r.read(64)
            assert b"--FRAME" in raw, raw[:64]


@test
def test_frames_actually_arrive_and_are_whole_jpegs():
    with Server() as s:
        with urllib.request.urlopen(s.url + "/stream", timeout=5) as r:
            assert s.wait_for_clients(1)
            threading.Thread(target=lambda: s.pump(8, 0.05), daemon=True).start()
            parts = read_parts(r, 3)
        assert len(parts) >= 3, f"only {len(parts)} frames arrived"
        for p in parts:
            assert p.startswith(b"\xff\xd8") and p.endswith(b"\xff\xd9"), p[:8]


@test
def test_nothing_is_encoded_until_somebody_is_watching():
    """The claim in preview.py's docstring: leaving this on costs nothing."""
    with Server() as s:
        assert s.preview.watching is False
        assert s.preview.state.clients == 0
        with urllib.request.urlopen(s.url + "/stream", timeout=3):
            assert s.wait_for_clients(1), "connecting didn't register a client"
            assert s.preview.watching is True


# -- the reaping bug --------------------------------------------------------

@test
def test_a_viewer_that_goes_away_is_noticed_while_frames_flow():
    with Server() as s:
        r = urllib.request.urlopen(s.url + "/stream", timeout=3)
        assert s.wait_for_clients(1)
        r.close()
        s.pump(20, 0.05)
        assert s.wait_for_clients(0), \
            f"{s.preview.state.clients} client(s) still counted after leaving"
        assert s.preview.watching is False


@test
def test_a_viewer_that_goes_away_is_noticed_even_when_frames_stop():
    """The bug this file was written for.

    A browser whose lid closed sends us nothing. If the producer also stops —
    the vision loop stalls, or the hand leaves and the caller pauses — then
    without a keepalive the handler blocks on its condition variable forever,
    the client is never reaped, and `watching` stays true for the life of the
    process. Every later frame is then encoded for an audience of nobody.
    """
    with Server() as s:
        r = urllib.request.urlopen(s.url + "/stream", timeout=3)
        assert s.wait_for_clients(1)
        s.publish()                     # one frame, then silence
        time.sleep(0.2)
        r.close()

        # Publish nothing at all from here on. Only the keepalive can notice.
        assert s.wait_for_clients(0, timeout=20), \
            "a departed viewer went unnoticed with no frames flowing — " \
            "clients never decremented, so watching is stuck true forever"
        assert s.preview.watching is False


@test
def test_a_second_viewer_can_connect_after_the_first_leaves():
    with Server() as s:
        r = urllib.request.urlopen(s.url + "/stream", timeout=3)
        assert s.wait_for_clients(1)
        r.close()
        s.pump(10, 0.05)
        assert s.wait_for_clients(0)

        with urllib.request.urlopen(s.url + "/stream", timeout=5) as r2:
            assert s.wait_for_clients(1), "reconnect didn't register"
            threading.Thread(target=lambda: s.pump(8, 0.05), daemon=True).start()
            assert read_parts(r2, 2), "frames didn't resume for the new viewer"


@test
def test_two_viewers_are_counted_separately():
    with Server() as s:
        a = urllib.request.urlopen(s.url + "/stream", timeout=3)
        b = urllib.request.urlopen(s.url + "/stream", timeout=3)
        assert s.wait_for_clients(2), s.preview.state.clients
        a.close()
        b.close()
        s.pump(20, 0.05)
        assert s.wait_for_clients(0), s.preview.state.clients


# -- lifecycle --------------------------------------------------------------

@test
def test_closing_the_server_frees_the_port():
    """Otherwise restarting the app after a crash says 'address in use' and
    looks like a dead camera."""
    port = free_port()
    Preview(port=port).close()
    time.sleep(0.1)
    second = Preview(port=port)          # would SystemExit if still held
    second.close()


@test
def test_a_busy_port_fails_with_something_actionable():
    with Server() as s:
        try:
            Preview(port=s.preview.port)
        except SystemExit as e:
            assert "pgrep" in str(e) or "busy" in str(e).lower(), e
            return
        raise AssertionError("a taken port should fail loudly, not silently")


@test
def test_the_server_leaves_no_threads_behind():
    before = {t.name for t in threading.enumerate()}
    for _ in range(5):
        with Server() as s:
            with urllib.request.urlopen(s.url + "/stream", timeout=3):
                s.wait_for_clients(1)
                s.pump(2, 0.02)
    time.sleep(1.0)
    leaked = {t.name for t in threading.enumerate()} - before
    assert not leaked, f"threads left running: {sorted(leaked)}"


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
