# Macro-time rejection of a list default whose elements don't match
# the spec's type=. Here ``type=int`` requires int elements; "two"
# is a string.
from tpy import int32
from argparse import ArgumentParser


def main() -> int32:
    parser = ArgumentParser()
    parser.add_argument("--n", type=int, action="append", default=[1, "two"])  # tpyc: error(/list default element must be int/)
    args = parser.parse_args([])
    print(args.n)
    return 0


main()
