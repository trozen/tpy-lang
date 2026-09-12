# required=True flag inside a sub-parser: the missing-required check
# fires on the sub's parse fn, exits with prog "<top> <sub>: error: ...".
# The top namespace still surfaces the field as Optional[str].
from tpy import int32
from argparse import ArgumentParser


def main() -> int32:
    parser = ArgumentParser(prog="prog")
    sub = parser.add_subparsers(dest="cmd", required=True)

    show = sub.add_parser("show")
    show.add_argument("--key", required=True)

    args = parser.parse_args(["show", "--key", "color"])
    print(args.cmd)
    if args.key is not None:
        print("key=" + args.key)
    return 0


main()
