# Runtime panic: positional token doesn't match any registered
# sub-parser name. Parser emits "invalid choice" + exits with 2.
from argparse import ArgumentParser
from tpy import Int32


def main() -> Int32:
    parser = ArgumentParser(prog="prog")
    sub = parser.add_subparsers(dest="cmd")
    show = sub.add_parser("show")
    set_p = sub.add_parser("set")
    args = parser.parse_args(["unknown"])
    return 0


main()
