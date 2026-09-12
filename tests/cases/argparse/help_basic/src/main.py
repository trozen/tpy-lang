# --help / -h dispatch (T1.A): bare parse_args() with -h or --help
# in argv prints the auto-generated help text and calls sys.exit(0)
# before any other dispatch runs. Confirms detection happens BEFORE
# missing-required-arg validation -- here ``file`` is required, but
# the bare argv carries -h so the help fn fires first.
from tpy import int32
from argparse import ArgumentParser


def main() -> int32:
    parser = ArgumentParser(description="A test program.")
    parser.add_argument("file", help="input file path")
    parser.add_argument("--count", type=int, default=1, help="repetition count")
    parser.add_argument("--name", default="world", help="greeting target")
    parser.add_argument("--verbose", "-v", action="count", help="increase verbosity")
    parser.add_argument("--tag", action="append", help="tag to apply (multi)")
    args = parser.parse_args(["-h"])
    # Unreachable: parse_args invokes the help printer, which exits(0).
    print(args.file)
    return 0


main()
