# action='store_const' requires const= to specify the value to store.
from tpy import Int32
from argparse import ArgumentParser


def main() -> Int32:
    parser = ArgumentParser()
    parser.add_argument("--mode", action="store_const", default="off")  # tpyc: error(/action='store_const' requires const=/)
    parser.parse_args([])
    return 0


main()
