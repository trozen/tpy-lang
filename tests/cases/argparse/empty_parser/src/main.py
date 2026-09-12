# Empty parser (no add_argument calls): parse_args([]) succeeds and
# returns an empty namespace.
from tpy import int32
from argparse import ArgumentParser


def main() -> int32:
    p = ArgumentParser()
    p.parse_args([])
    print("ok")
    return 0


main()
