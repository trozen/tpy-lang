# argparse store_true / store_false / count actions (Phase 7D2).
# These actions do not consume an argv token after the flag.
from tpy import int32
from argparse import ArgumentParser


def main() -> int32:
    parser = ArgumentParser()
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--no-cache", action="store_false")
    parser.add_argument("-c", action="count", default=0)
    args = parser.parse_args(["--verbose", "-c", "-c", "-c"])
    print(args.verbose)
    print(args.no_cache)
    print(args.c)
    return 0


main()
