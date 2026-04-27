# Empty parser raises ValueError on any unrecognized token.
from tpy import Int32
from argparse import ArgumentParser


def main() -> Int32:
    p = ArgumentParser()
    p.parse_args(["unexpected"])
    return 0


main()
