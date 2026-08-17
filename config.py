"""Load config.toml.

Its own module so the camera scripts and the gesture layer can both read the
same file without one importing the other.
"""

import os
import tomllib

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.toml")


def load_config(path=PATH):
    with open(path, "rb") as f:
        return tomllib.load(f)


def rotation(cli_value=None):
    """How far to rotate the frame, in degrees.

    The camera is mounted at an angle, so this is a property of the hardware
    rather than something to remember at the command line — it lives in
    config.toml, and --rotate overrides it when experimenting.
    """
    if cli_value is not None:
        return cli_value
    return load_config().get("camera", {}).get("rotate", 0)
