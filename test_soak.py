"""Run it for a long time and see what accumulates.

Every other test in this repo finishes in seconds, which means none of them can
see the failures that only show up over an evening: a thread that is started
per reconnect and never joined, a socket left open every time a viewer leaves,
a buffer that grows a little each frame. The device is meant to sit plugged in
all day, so those are exactly the failures that would find you and not us.

Method is the same for all three: snapshot, run a lot of cycles, snapshot, and
assert growth is **bounded** — not zero. Python's allocator, interned strings
and free lists all guarantee a small nonzero delta, so a `== 0` assertion
flakes forever and teaches you to ignore it.

    python3 test_soak.py --minutes 20
    python3 test_soak.py --minutes 2        a quick smoke check

Runs anywhere. The Bluetooth cycles are skipped off the Pi, and say so.
"""

import argparse
import faulthandler
import gc
import os
import subprocess
import sys
import threading
import time
import tracemalloc

from handsfree import scripts
from handsfree.config import load_config
from handsfree.driver import Driver
from handsfree.gestures import GestureEngine
from handsfree.transport.null import Backend as Null

CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


# -- what we can measure without any dependencies ---------------------------

def threads():
    return sorted(t.name for t in threading.enumerate())


def fds():
    """Open file descriptors. /proc on Linux, lsof on macOS, None if neither."""
    try:
        return len(os.listdir("/proc/self/fd"))
    except OSError:
        pass
    try:
        out = subprocess.run(["lsof", "-p", str(os.getpid())],
                             capture_output=True, text=True).stdout
        return len(out.splitlines())
    except (FileNotFoundError, OSError):
        return None


def rss_kb():
    """Resident memory. The only way to see C-extension growth, which is where
    OpenCV's JPEG encoder would leak — tracemalloc cannot see it at all."""
    try:
        with open("/proc/self/statm") as fh:
            return int(fh.read().split()[1]) * (os.sysconf("SC_PAGE_SIZE") // 1024)
    except OSError:
        pass
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())],
                             capture_output=True, text=True).stdout
        return int(out.strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


class Watch:
    """Before/after on everything we can count."""

    def __init__(self, label):
        self.label = label
        gc.collect()
        self.threads = threads()
        self.fds = fds()
        self.rss = rss_kb()
        self.snapshot = tracemalloc.take_snapshot()

    def report(self, thread_slack=0, fd_slack=4, rss_slack_kb=20_000):
        gc.collect()
        time.sleep(0.3)                  # let daemon threads finish unwinding
        gc.collect()

        problems = []
        leaked = [t for t in threads() if t not in self.threads]
        if len(leaked) > thread_slack:
            problems.append(f"threads leaked: {leaked}")

        now_fds = fds()
        if self.fds is not None and now_fds is not None:
            if now_fds - self.fds > fd_slack:
                problems.append(
                    f"file descriptors grew {self.fds} -> {now_fds}")

        now_rss = rss_kb()
        if self.rss is not None and now_rss is not None:
            if now_rss - self.rss > rss_slack_kb:
                problems.append(
                    f"RSS grew {self.rss} -> {now_rss} KB "
                    f"(+{now_rss - self.rss})")

        top = tracemalloc.take_snapshot().compare_to(self.snapshot, "lineno")
        biggest = top[0] if top else None

        print(f"    {self.label}: threads {len(self.threads)}->"
              f"{len(threads())}  fds {self.fds}->{now_fds}  "
              f"rss {self.rss}->{now_rss} KB")
        if biggest and biggest.size_diff > 512 * 1024:
            print(f"      largest python-heap growth: "
                  f"{biggest.size_diff // 1024} KB at {biggest.traceback}")

        assert not problems, f"{self.label}: " + "; ".join(problems)


def session(name="demo", cfg=None):
    """One scripted session through engine and driver, as fast as it will go."""
    cfg = cfg or load_config()
    transport = Null(quiet=True)
    driver = Driver(transport, cfg.get("hid", {}), thread=False)
    engine = GestureEngine(cfg)
    try:
        for t, landmarks in scripts.build(name):
            driver.clock = lambda t=t: t
            driver.handle(engine.update(landmarks, t))
            driver.pump()
        return driver.buttons
    finally:
        driver.close()


# -- the checks -------------------------------------------------------------

@check
def check_repeated_sessions_do_not_accumulate(deadline):
    """The core loop, over and over. Anything the engine or driver holds on to
    between sessions shows up here."""
    session()                            # warm up: first run allocates caches
    watch = Watch("sessions")
    n = 0
    names = scripts.names()
    while time.monotonic() < deadline:
        held = session(names[n % len(names)])
        assert held == 0, f"session {n} left the button down"
        n += 1
    print(f"    ran {n} sessions")
    watch.report()


@check
def check_drivers_start_and_stop_cleanly(deadline):
    """Every Driver starts a thread. If close() doesn't stop it, an all-day
    session ends up with hundreds."""
    cfg = load_config().get("hid", {})
    Driver(Null(quiet=True), cfg).close()
    watch = Watch("driver threads")
    n = 0
    while time.monotonic() < deadline:
        driver = Driver(Null(quiet=True), cfg)      # a real thread this time
        driver.pump()
        driver.close()
        assert not driver.thread_stuck, f"cycle {n}: cursor thread outlived close"
        n += 1
    print(f"    started and stopped {n} drivers")
    watch.report()


@check
def check_the_preview_server_does_not_accumulate(deadline):
    """Create, connect, drop, close — the cycle a browser tab makes all day."""
    import urllib.request

    from test_preview import JPEG, Server

    Server().close()
    watch = Watch("preview")
    n = 0
    while time.monotonic() < deadline:
        server = Server()
        try:
            with urllib.request.urlopen(server.url + "/stream", timeout=3):
                server.wait_for_clients(1)
                server.publish(JPEG)
                time.sleep(0.02)
            server.pump(3, 0.01)
            assert server.wait_for_clients(0, timeout=8), \
                f"cycle {n}: viewer never reaped"
        finally:
            server.close()
        n += 1
    print(f"    ran {n} preview servers")
    watch.report()


@check
def check_the_bluetooth_transport_can_be_cycled(deadline):
    """Bring the real radio up and down repeatedly. Catches exactly the two
    leaks found earlier: L2CAP ports still claimed by a parked accept thread,
    and a D-Bus agent never unregistered — either of which makes the *second*
    run of the day fail to start."""
    if not (sys.platform == "linux" and os.path.exists("/sys/class/bluetooth")):
        print("    skipped — needs the Pi")
        return
    if os.geteuid() != 0:
        print("    skipped — needs root for PSM 17/19")
        return

    from handsfree.transport.bluetooth import Backend

    cfg = load_config().get("hid", {})
    Backend(cfg, verbose=False).close()
    watch = Watch("bluetooth")
    n = 0
    while time.monotonic() < deadline:
        wire = Backend(cfg, verbose=False)
        try:
            wire.send_mouse(b"\x02\x00\x00\x00\x00\x00\x00\x00")
        finally:
            wire.close()
        n += 1
    print(f"    brought the radio up and down {n} times")
    watch.report(fd_slack=6)


def main():
    ap = argparse.ArgumentParser(prog="python3 test_soak.py")
    ap.add_argument("--minutes", type=float, default=20.0,
                    help="total wall time, split across the checks")
    args = ap.parse_args()

    # If a shutdown wedges, print every thread's stack instead of just hanging.
    faulthandler.enable()
    budget = args.minutes * 60
    faulthandler.dump_traceback_later(budget + 120, exit=True)

    tracemalloc.start()
    per = budget / len(CHECKS)
    print(f"soaking for {args.minutes:g} minutes "
          f"({per / 60:.1f} min per check)\n")

    failed = []
    for fn in CHECKS:
        print(f"  {fn.__name__}")
        deadline = time.monotonic() + per
        try:
            fn(deadline)
            print(f"  pass  {fn.__name__}\n")
        except AssertionError as e:
            failed.append(fn.__name__)
            print(f"  FAIL  {fn.__name__}\n          {e}\n")
        except Exception as e:
            failed.append(fn.__name__)
            print(f"  ERROR {fn.__name__}\n          "
                  f"{type(e).__name__}: {e}\n")

    print(f"{len(CHECKS) - len(failed)}/{len(CHECKS)} pass")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
