# required= is meaningless for positionals (they're always required).
from tpy import int32
from argparse import ArgumentParser


def main() -> int32:
    parser = ArgumentParser()
    parser.add_argument("name", required=True)  # tpyc: error(/required= is meaningless for positional arguments/)
    parser.parse_args(["alice"])
    return 0


main()
