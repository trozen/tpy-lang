# Empty parser (no add_argument calls): parse_args([]) succeeds and
# returns an empty namespace.
from tpy import Int32
from argparse import ArgumentParser


def main() -> Int32:
    p = ArgumentParser()
    p.parse_args([])
    print("ok")
    return 0


main()
