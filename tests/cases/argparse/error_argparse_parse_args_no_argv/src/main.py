# parse_args() with no arguments isn't yet supported -- CPython's
# sys.argv[1:] fallback would require macro_deps wiring for builder-
# trace macros, which isn't in place.
from tpy import Int32
from argparse import ArgumentParser


def main() -> Int32:
    parser = ArgumentParser()
    parser.add_argument("name")
    args = parser.parse_args()  # tpyc: error(/parse_args\(\) requires explicit argv/)
    print(args.name)
    return 0


main()
