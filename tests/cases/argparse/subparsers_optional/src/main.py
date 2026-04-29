# Subparsers with required=False: subcommand may be absent. cmd
# becomes Optional[str] and every per-sub field is Optional[T].
from tpy import Int32
from argparse import ArgumentParser


def main() -> Int32:
    parser = ArgumentParser(prog="prog")
    sub = parser.add_subparsers(dest="cmd")
    a = sub.add_parser("a")
    a.add_argument("--x")

    args = parser.parse_args([])
    # cmd defaults to None under both backends when required=False
    # and no sub-parser was chosen. Per-sub fields aren't read here:
    # under CPython argparse they don't exist on the Namespace at all
    # when no sub matched, while TPy stores them as None.
    print("cmd is None: " + str(args.cmd is None))
    return 0


main()
