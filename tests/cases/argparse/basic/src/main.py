# Minimal argparse via builder-trace macro: a single positional string
# argument and parse_args on a hardcoded argv list. Same source runs
# under CPython (resolving to the stdlib argparse module).
from tpy import Int32
from argparse import ArgumentParser


def main() -> Int32:
    parser = ArgumentParser()
    parser.add_argument("name")
    args = parser.parse_args(["alice"])
    print(args.name)
    return 0


main()
