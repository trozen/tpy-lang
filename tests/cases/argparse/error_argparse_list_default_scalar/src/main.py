# Macro-time rejection of a list default on a scalar-typed action.
from tpy import int32
from argparse import ArgumentParser


def main() -> int32:
    parser = ArgumentParser()
    parser.add_argument("--name", default=[1, 2])  # tpyc: error(/list default is only valid/)
    args = parser.parse_args([])
    print(args.name)
    return 0


main()
