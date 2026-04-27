# A required positional that's absent triggers the parse-error
# path: usage line + ``prog: error: ...`` to stderr, then sys.exit(2).
from tpy import Int32
from argparse import ArgumentParser


def main() -> Int32:
    parser = ArgumentParser()
    parser.add_argument("file")
    parser.parse_args([])
    return 0


main()
