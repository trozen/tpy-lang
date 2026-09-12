# prog/epilog: ArgumentParser kwargs that customize the help text.
# ``prog`` replaces the default ``"prog"`` placeholder in the usage
# line and the ``<prog>: error: ...`` parse-error prefix; ``epilog``
# is appended after the options block in --help output.
from tpy import int32
from argparse import ArgumentParser


def main() -> int32:
    parser = ArgumentParser(
        prog="myapp",
        description="Frobnicate widgets.",
        epilog="See myapp(1) for further details.",
    )
    parser.add_argument("file", help="input file path")
    parser.add_argument("--count", type=int, default=1, help="repetition count")
    args = parser.parse_args(["-h"])
    # Unreachable: parse_args invokes the help printer, which exits(0).
    print(args.file)
    return 0


main()
