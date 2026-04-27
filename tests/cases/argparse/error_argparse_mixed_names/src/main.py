# Positional and optional flag names cannot be mixed in a single
# add_argument call.
from tpy import Int32
from argparse import ArgumentParser


def main() -> Int32:
    parser = ArgumentParser()
    parser.add_argument("name", "--foo")  # tpyc: error(/positional and optional names cannot be mixed/)
    parser.parse_args([])
    return 0


main()
