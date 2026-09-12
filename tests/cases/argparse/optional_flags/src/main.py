# argparse optional flags with type= and default= (Phase 7D1).
# Exercises -short / --long aliases, type=int / type=float / type=str
# conversion, and default values used when the flag is absent.
from tpy import int32
from argparse import ArgumentParser


def main() -> int32:
    parser = ArgumentParser()
    parser.add_argument("-n", "--count", type=int, default=1)
    parser.add_argument("--scale", type=float, default=1.5)
    parser.add_argument("--name", default="world")
    args = parser.parse_args(["--count", "5", "--name", "alice"])
    print(args.count)
    print(args.scale)
    print(args.name)
    return 0


main()
