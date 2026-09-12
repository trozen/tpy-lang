# Subparsers + a top-level common flag. The common flag stays on
# the top namespace as `args.verbose`; per-sub fields land on the
# top namespace as Optional[T].
from tpy import int32
from argparse import ArgumentParser


def main() -> int32:
    parser = ArgumentParser(prog="prog")
    parser.add_argument("-v", "--verbose", action="store_true")

    sub = parser.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("a")
    a.add_argument("--x")
    b = sub.add_parser("b")
    b.add_argument("--y")

    args = parser.parse_args(["-v", "a", "--x", "hello"])
    print("verbose=" + str(args.verbose))
    print("cmd=" + args.cmd)
    # args.x is set under both backends because sub "a" was chosen.
    # Don't read args.y here -- under CPython argparse only the
    # chosen sub's attributes exist, while TPy populates every
    # per-sub field as Optional[T] on the top namespace.
    if args.x is not None:
        print("x=" + args.x)
    return 0


main()
