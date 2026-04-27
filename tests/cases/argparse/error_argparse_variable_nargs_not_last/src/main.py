# Variable-length nargs ('*' / '+' / '?') on a positional must be the
# last positional, otherwise consumption is ambiguous.
from tpy import Int32
from argparse import ArgumentParser


def main() -> Int32:
    parser = ArgumentParser()
    parser.add_argument("files", nargs="+")
    parser.add_argument("dest")  # the '+' positional must be last
    args = parser.parse_args(["a", "b", "c"])  # tpyc: error(/with nargs='\+' must be the last positional/)
    print(args.dest)
    return 0


main()
