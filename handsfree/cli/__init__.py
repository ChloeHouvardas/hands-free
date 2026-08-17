"""Command-line entry points. Everything hardware-facing lives here."""

import argparse


def parser(command, **kwargs):
    """An ArgumentParser whose usage line is the command you'd actually retype.

    Without this argparse infers the program name from how Python was invoked,
    which drops the subcommand and prints `python3 -m handsfree [--check]` —
    copy that and it fails.
    """
    return argparse.ArgumentParser(prog=f"python3 -m handsfree {command}",
                                   **kwargs)
