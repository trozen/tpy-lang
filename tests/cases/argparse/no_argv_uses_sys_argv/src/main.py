# Bare parse_args() with no arguments falls back to sys.argv[1:]
# (D2: matches CPython argparse). The test runs with no program
# arguments, so sys.argv[1:] is empty and all flags fall back to
# their defaults.
from tpy import int32
from argparse import ArgumentParser


def main() -> int32:
    parser = ArgumentParser()
    parser.add_argument("--name", default="world")
    parser.add_argument("--count", type=int, default=1)
    args = parser.parse_args()
    print(args.name)
    print(args.count)
    return 0


main()
