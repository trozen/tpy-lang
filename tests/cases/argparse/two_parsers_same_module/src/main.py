# Two functions each opening an independent ArgumentParser trace in
# the same module: the synthesized records/functions must get distinct
# names rather than colliding on __tpy_builder_argparse_args_1.
from tpy import Int32
from argparse import ArgumentParser


def parse_a() -> Int32:
    parser = ArgumentParser()
    parser.add_argument("name")
    args = parser.parse_args(["alice"])
    print(args.name)
    return 0


def parse_b() -> Int32:
    parser = ArgumentParser()
    parser.add_argument("--count", type=int, default=0)
    args = parser.parse_args(["--count", "7"])
    print(args.count)
    return 0


def main() -> Int32:
    parse_a()
    parse_b()
    return 0


main()
