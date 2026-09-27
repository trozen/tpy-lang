# `list(...)` of a slice into a local that a later statement rebinds.
import sys


def main() -> None:
    argv = list(sys.argv[1:])
    if len(argv) == 0:
        argv = ["--help"]
    print(argv)


main()
