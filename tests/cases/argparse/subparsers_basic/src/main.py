# Subparsers via builder-trace macro: two sub-commands ("show" / "set")
# selected by the first positional. Subcommand-specific args appear
# as Optional[T] fields on the top namespace (CPython argparse
# Namespace shape); the chosen subcommand name lands in args.cmd.
from tpy import Int32
from argparse import ArgumentParser


def main() -> Int32:
    parser = ArgumentParser(prog="prog")
    sub = parser.add_subparsers(dest="cmd", required=True)

    show = sub.add_parser("show")
    show.add_argument("--key")

    set_p = sub.add_parser("set")
    set_p.add_argument("--key")
    set_p.add_argument("--value")

    args = parser.parse_args(["set", "--key", "color", "--value", "blue"])
    print(args.cmd)
    if args.key is not None:
        print("key=" + args.key)
    if args.value is not None:
        print("value=" + args.value)
    return 0


main()
