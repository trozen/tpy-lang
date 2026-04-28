# Usage line wrapping at 80 cols. When the joined usage tokens
# exceed _USAGE_TEXT_WIDTH (80), the help printer wraps the line
# CPython-style: prog stays on row 1 (since it fits within 75% of
# the width), and continuation lines indent to align past it.
from tpy import Int32
from argparse import ArgumentParser


def main() -> Int32:
    parser = ArgumentParser(
        prog="frobnicator",
        description="Many-flag CLI to exercise usage-line wrap.",
    )
    parser.add_argument("--alpha", help="alpha flag")
    parser.add_argument("--bravo", help="bravo flag")
    parser.add_argument("--charlie", help="charlie flag")
    parser.add_argument("--delta", help="delta flag")
    parser.add_argument("--echo", help="echo flag")
    parser.add_argument("--foxtrot", help="foxtrot flag")
    parser.add_argument("--golf", help="golf flag")
    parser.add_argument("input")
    args = parser.parse_args(["-h"])
    print(args.input)
    return 0


main()
