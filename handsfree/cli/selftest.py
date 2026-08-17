"""Run every suite that makes sense on this machine, and say so in one place.

    python3 -m handsfree selftest              on a laptop
    sudo venv/bin/python -m handsfree selftest  on the Pi, for the whole lot

The point is that there is one command to run before a manual session, and it
knows what it can't check here rather than passing quietly. A suite that needs
`/dev/uhid`, or root, or a Bluetooth adapter is reported **skipped**, with the
reason — a green run on a laptop and a green run on the Pi mean different
things, and the output has to say which one you got.

The soak is excluded: it takes twenty minutes by design. `--soak` adds it.
"""

import os
import subprocess
import sys

from handsfree.cli import parser
from handsfree.config import ROOT

#: (file, what it proves, what it needs)
SUITES = [
    ("test_gestures.py", "the state machine, on generated hands", None),
    ("test_hid.py", "report descriptors and report bytes", None),
    ("test_driver.py", "the cursor, the guards, and shutdown", None),
    ("test_scenarios.py", "whole sessions, end to end", None),
    ("test_preview.py", "the live-view server and its client bookkeeping", None),
    ("test_transport.py", "the socket layer (more of it on the Pi)", None),
    ("test_uhid.py", "our HID bytes, against the kernel's own parser", "linux"),
]


def missing(needs):
    """Why this suite can't run here, or None if it can."""
    if needs == "linux" and sys.platform != "linux":
        return f"needs Linux and /dev/uhid, this is {sys.platform}"
    if needs == "linux" and not os.path.exists("/dev/uhid"):
        return "no /dev/uhid on this kernel"
    if needs == "linux" and os.geteuid() != 0:
        return "needs root — /dev/uhid is 0600"
    return None


def run(argv, stream=False):
    """Run one suite. Returns (ok, its tally line, the completed process)."""
    if stream:
        # The soak prints progress for twenty minutes; capturing that and
        # showing it at the end would be useless.
        done = subprocess.run([sys.executable, *argv], cwd=ROOT)
        return done.returncode == 0, "", None
    done = subprocess.run([sys.executable, *argv], cwd=ROOT,
                          capture_output=True, text=True)
    lines = [ln for ln in (done.stdout + done.stderr).splitlines() if ln.strip()]
    tally = next((ln for ln in reversed(lines) if "pass" in ln), "")
    return done.returncode == 0, tally.strip(), done


def main():
    ap = parser("selftest")
    ap.add_argument("--soak", action="store_true",
                    help="also run the 20-minute leak check")
    ap.add_argument("--minutes", type=float, default=20.0,
                    help="how long --soak runs for")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print the full output of anything that fails")
    args = ap.parse_args()

    suites = [(name, blurb, needs, [name]) for name, blurb, needs in SUITES]
    if args.soak:
        suites.append(("test_soak.py",
                       "threads, file descriptors and memory over time", None,
                       ["test_soak.py", "--minutes", f"{args.minutes:g}"]))

    print(f"  {'suite':<20} {'result':<12} what it proves")
    print(f"  {'-' * 20} {'-' * 12} {'-' * 44}")

    failed, skipped, ran = [], [], 0
    for name, blurb, needs, argv in suites:
        why = missing(needs)
        if why is None and not os.path.exists(os.path.join(ROOT, name)):
            why = "not present"
        if why:
            skipped.append(name)
            print(f"  {name:<20} {'skip':<12} {why}")
            continue

        if name == "test_soak.py":
            print(f"  {name:<20} {'running':<12} {blurb}\n")
        ok, tally, result = run(argv, stream=(name == "test_soak.py"))
        ran += 1
        if not ok:
            failed.append(name)
        print(f"  {name:<20} {tally or ('ok' if ok else 'FAILED'):<12} {blurb}")
        if not ok and args.verbose and result is not None:
            for line in (result.stdout + result.stderr).splitlines():
                if "FAIL" in line or "ERROR" in line or line.startswith("  "):
                    print(f"        {line}")

    print()
    if skipped:
        print(f"  {len(skipped)} suite(s) skipped on this machine: "
              f"{', '.join(skipped)}")
        if sys.platform != "linux":
            print("  Run it on the Pi with sudo for the hardware half.")
    if failed:
        print(f"\n  {len(failed)}/{ran} suites FAILED: {', '.join(failed)}")
        return 1
    print(f"\n  all {ran} suites pass"
          + (" — but see the skips above" if skipped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
