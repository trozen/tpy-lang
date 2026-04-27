# nargs= must be one of '?' / '*' / '+' or a positive integer.
from tpy import Int32
from argparse import ArgumentParser


def main() -> Int32:
    parser = ArgumentParser()
    parser.add_argument("--values", nargs=0)  # tpyc: error(/nargs= must be a positive integer/)
    parser.parse_args([])
    return 0


main()
