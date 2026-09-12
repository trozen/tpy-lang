# metavar=: per-argument display name override for the synthesized
# usage line and --help output. Without metavar=, flags use the
# uppercase dest (CPython convention) and positionals use their
# literal name. With metavar=, the override appears everywhere the
# value-taking arg is referenced in help text.
from tpy import int32
from argparse import ArgumentParser


def main() -> int32:
    parser = ArgumentParser(description="Test metavar override.")
    parser.add_argument("path", metavar="PATH", help="file path")
    parser.add_argument("--count", type=int, metavar="N", default=1, help="count")
    parser.add_argument("--names", action="append", metavar="NAME", help="names")
    args = parser.parse_args(["-h"])
    print(args.path)
    return 0


main()
