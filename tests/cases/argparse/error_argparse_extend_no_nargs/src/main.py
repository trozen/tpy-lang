# action='extend' without nargs= is rejected (CPython would iterate
# the converted value with surprising semantics).
from tpy import int32
from argparse import ArgumentParser


def main() -> int32:
    parser = ArgumentParser()
    parser.add_argument("--tag", action="extend")  # tpyc: error(/action='extend' requires nargs=/)
    parser.parse_args([])
    return 0


main()
