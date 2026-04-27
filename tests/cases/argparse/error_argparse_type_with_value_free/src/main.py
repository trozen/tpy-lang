# type= cannot be combined with value-free actions like store_true.
from tpy import Int32
from argparse import ArgumentParser


def main() -> Int32:
    parser = ArgumentParser()
    parser.add_argument("-v", action="store_true", type=int)  # tpyc: error(/type= cannot be combined with action='store_true'/)
    parser.parse_args([])
    return 0


main()
