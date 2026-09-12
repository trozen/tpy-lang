# A value not in choices= triggers the parse-error path: usage line
# + ``prog: error: invalid choice ...`` to stderr, then sys.exit(2).
from tpy import int32
from argparse import ArgumentParser


def main() -> int32:
    parser = ArgumentParser()
    parser.add_argument("--mode", choices=("auto", "manual"))
    parser.parse_args(["--mode", "bogus"])
    return 0


main()
