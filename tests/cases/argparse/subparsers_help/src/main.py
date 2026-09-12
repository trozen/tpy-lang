# Subparsers --help dispatch: top-level -h prints usage with the
# {a,b} placeholder + per-sub-parser rows in the positional section.
# prog= is passed explicitly so the cpy phase compares byte-identical
# output against CPython's stdlib argparse.
from tpy import int32
from argparse import ArgumentParser


def main() -> int32:
    parser = ArgumentParser(prog="prog", description="A test program.")
    parser.add_argument("--verbose", "-v", action="store_true", help="be loud")

    sub = parser.add_subparsers(dest="cmd", required=True, help="sub-command")
    show = sub.add_parser("show", help="show a value")
    show.add_argument("--key", help="key to show")
    set_p = sub.add_parser("set", help="set a value")
    set_p.add_argument("--key", help="key to set")
    set_p.add_argument("--value", help="new value")

    args = parser.parse_args(["-h"])
    # Unreachable: parse_args invokes the help printer, which exits(0).
    print(args.cmd)
    return 0


main()
