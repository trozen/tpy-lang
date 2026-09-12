# action= must be one of the supported strings.
from tpy import int32
from argparse import ArgumentParser


def main() -> int32:
    parser = ArgumentParser()
    parser.add_argument("--mode", action="frob")  # tpyc: error(/action='frob' is not supported/)
    parser.parse_args([])
    return 0


main()
