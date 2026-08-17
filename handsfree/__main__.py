"""One entry point for everything.

    python3 -m handsfree run                              the live app
    python3 -m handsfree replay 'recordings/*.jsonl' --check

Each subcommand is a module in handsfree/cli/. Nothing is imported until it's
asked for, so `-m handsfree replay` works on a laptop with no camera libraries
installed even though `run` and `record` would fail there.
"""

import importlib
import sys

COMMANDS = {
    "run": "the live app — camera to gesture events on stdout",
    "record": "record a landmark session to recordings/*.jsonl",
    "replay": "replay recordings through the gesture layer",
    "bench": "FPS, latency, CPU and jitter",
    "capture": "camera only, no ML — to tell the two halves apart",
    "landmarks": "draw the 21 landmarks on the live view",
}


def usage(stream=sys.stdout):
    print("usage: python3 -m handsfree <command> [options]\n", file=stream)
    print("commands:", file=stream)
    for name, blurb in COMMANDS.items():
        print(f"  {name:<10} {blurb}", file=stream)
    print("\nPass --help to any command for its options.", file=stream)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv

    if not argv:
        usage(sys.stderr)
        return 2
    if argv[0] in ("-h", "--help"):
        usage()
        return 0

    command, rest = argv[0], argv[1:]
    if command not in COMMANDS:
        print(f"unknown command: {command}\n", file=sys.stderr)
        usage(sys.stderr)
        return 2

    module = importlib.import_module(f"handsfree.cli.{command}")
    # argparse takes its program name from argv[0]. Set it to the command as
    # you'd actually retype it, so usage lines are copy-pasteable.
    sys.argv = [f"python3 -m handsfree {command}", *rest]
    return module.main()


if __name__ == "__main__":
    sys.exit(main())
