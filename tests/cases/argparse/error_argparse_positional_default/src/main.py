# Positional arguments may only specify default= when nargs='?'
# (otherwise they are always required and the default is unreachable).
from tpy import int32
from argparse import ArgumentParser


def main() -> int32:
    parser = ArgumentParser()
    parser.add_argument("name", default="anon")  # tpyc: error(/positional arguments may only specify default= when nargs='\?'/)
    parser.parse_args(["alice"])
    return 0


main()
