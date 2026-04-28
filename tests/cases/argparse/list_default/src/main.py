# default=[...]: list-literal default for list-typed actions
# (action=append/extend or store + nargs=*/+/<int>). When the flag
# is absent from argv, the field is initialized with the default
# list rather than None or []; when the flag is given, the values
# replace (store + nargs=+) or append onto the default (action=append).
# Two parsers because builder-trace requires one parse_args per parser.
from tpy import Int32
from argparse import ArgumentParser


def absent_case() -> None:
    parser = ArgumentParser(description="Test list-literal defaults.")
    parser.add_argument("--tag", action="append", default=["alpha", "beta"])
    parser.add_argument("--count", type=int, nargs="+", default=[1, 2, 3])
    args = parser.parse_args([])
    print(args.tag)
    print(args.count)


def present_case() -> None:
    parser = ArgumentParser(description="Test list-literal defaults.")
    parser.add_argument("--tag", action="append", default=["alpha", "beta"])
    parser.add_argument("--count", type=int, nargs="+", default=[1, 2, 3])
    args = parser.parse_args(["--tag", "gamma", "--count", "10", "20"])
    print(args.tag)
    print(args.count)


def main() -> Int32:
    absent_case()
    present_case()
    return 0


main()
