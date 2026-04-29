# Same per-sub field name with required=True in one sub and the
# default (not required) in another. Both unify to a single
# Optional[str] field on the top namespace -- the required=True
# enforcement happens inside the sub's parse fn, while the top-level
# field type accepts None for the case where a different sub was
# chosen and the field wasn't set.
from tpy import Int32
from argparse import ArgumentParser


def main() -> Int32:
    parser = ArgumentParser(prog="prog")
    sub = parser.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("a")
    a.add_argument("--x", required=True)

    b = sub.add_parser("b")
    b.add_argument("--x")  # not required

    args = parser.parse_args(["b"])
    print(args.cmd)
    print("x is None: " + str(args.x is None))
    return 0


main()
