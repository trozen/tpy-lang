# Sub-parser with a positional argument: the positional lives on the
# sub's own parse fn, and the field surfaces as Optional[T] on the
# top namespace under the flat-namespace layout.
from tpy import int32
from argparse import ArgumentParser


def main() -> int32:
    parser = ArgumentParser(prog="prog")
    sub = parser.add_subparsers(dest="cmd", required=True)

    show = sub.add_parser("show")
    show.add_argument("filename")

    set_p = sub.add_parser("set")
    set_p.add_argument("--value")

    args = parser.parse_args(["show", "config.toml"])
    print(args.cmd)
    if args.filename is not None:
        print("filename=" + args.filename)
    return 0


main()
