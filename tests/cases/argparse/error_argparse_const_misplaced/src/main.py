# const= is only valid with action='store_const' or action='store' + nargs='?'.
from tpy import Int32
from argparse import ArgumentParser


def main() -> Int32:
    parser = ArgumentParser()
    parser.add_argument("--mode", const="x", default="y")  # tpyc: error(/const= is only valid/)
    parser.parse_args([])
    return 0


main()
