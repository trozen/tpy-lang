# usage="...": full override of the auto-generated usage tail. The
# resulting usage line is ``"usage: " + <user-string>``; the macro
# does NOT auto-render flags / positionals into it. Useful when the
# user wants a hand-written summary (CPython argparse parity).
from tpy import Int32
from argparse import ArgumentParser


def main() -> Int32:
    parser = ArgumentParser(
        prog="myapp",
        usage="myapp [--count N] FILE",
        description="Frobnicate widgets.",
    )
    parser.add_argument("file", help="input file path")
    parser.add_argument("--count", type=int, default=1, help="repetition count")
    args = parser.parse_args(["-h"])
    print(args.file)
    return 0


main()
