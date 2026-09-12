# add_help=False: opt out of the auto-generated -h / --help printer
# and reservation. With this flag, the user can register their own
# --help handler (or use -h for an unrelated purpose). This test
# registers a custom -h flag with action="store_true" so the parsed
# record exposes ``args.h`` rather than the macro emitting a help
# printer + sys.exit(0) prelude.
from tpy import int32
from argparse import ArgumentParser


def main() -> int32:
    parser = ArgumentParser(add_help=False, description="No auto-help.")
    parser.add_argument("-h", action="store_true", help="terse mode")
    parser.add_argument("--count", type=int, default=1)
    args = parser.parse_args(["-h", "--count", "3"])
    print(args.h, args.count)
    return 0


main()
