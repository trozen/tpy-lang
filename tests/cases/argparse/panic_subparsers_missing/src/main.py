# Runtime panic: required=True subcommand is missing from argv.
# Parser writes the error to stderr and exits with code 2.
from argparse import ArgumentParser
from tpy import int32


def main() -> int32:
    parser = ArgumentParser(prog="prog")
    sub = parser.add_subparsers(dest="cmd", required=True)
    show = sub.add_parser("show")
    set_p = sub.add_parser("set")
    args = parser.parse_args([])
    print(args.cmd)
    return 0


main()
