# Positional arguments only support action='store'; the count /
# store_true / etc. actions are flag-only.
from tpy import int32
from argparse import ArgumentParser


def main() -> int32:
    parser = ArgumentParser()
    parser.add_argument("name", action="count")  # tpyc: error(/action='count' is only valid for optional flags/)
    parser.parse_args([])
    return 0


main()
