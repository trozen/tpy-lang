# Runtime parse-error path with prog= and usage= overrides. Verifies
# that ``prog`` substitutes through the ``<prog>: error: ...`` prefix
# inlined at the error site, and that a custom ``usage=`` line is the
# usage shown above the error message. Stderr must contain
# ``"myapp: error: ..."`` and the usage override.
from tpy import int32
from argparse import ArgumentParser


def main() -> int32:
    parser = ArgumentParser(
        prog="myapp",
        usage="myapp [--count N]",
    )
    parser.add_argument("--count", type=int, default=1)
    args = parser.parse_args(["--unknown"])
    print(args.count)
    return 0


main()
