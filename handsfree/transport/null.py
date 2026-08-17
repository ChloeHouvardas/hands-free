"""A transport that goes nowhere, for working without hardware.

Same job `capture.py` does for the camera: when the cursor misbehaves, being
able to watch the exact bytes on a laptop tells you immediately whether the
problem is upstream of the wire or on it.

    python3 -m handsfree replay recordings/drag.jsonl --drive
"""

import sys

from handsfree.transport import Transport


class Backend(Transport):
    name = "null"

    def __init__(self, cfg=None, stream=None, quiet=False):
        self.cfg = cfg or {}
        self.stream = stream or sys.stdout
        self.quiet = quiet
        #: Every report sent, so tests can assert on the whole session.
        self.sent = []

    def send_mouse(self, report):
        self._log("mouse", report)

    def send_keyboard(self, report):
        self._log("keys", report)

    def _log(self, kind, report):
        self.sent.append((kind, bytes(report)))
        if not self.quiet:
            print(f"  {kind}  {bytes(report).hex(' ')}", file=self.stream,
                  flush=True)

    def close(self):
        if not self.quiet:
            print(f"  null transport closed after {len(self.sent)} reports",
                  file=self.stream, flush=True)
