"""Compatibility launcher; the PC program now starts through tabletop_os.cli."""
import sys

from tabletop_os.system.coordinates import project_between
from tabletop_os.system.display import pointer_frame


def main(argv=None):
    from tabletop_os.cli import main as run_program
    arguments = sys.argv[1:] if argv is None else argv
    return run_program(["--app", "gesture-drag", *arguments])


if __name__ == "__main__":
    raise SystemExit(main())
