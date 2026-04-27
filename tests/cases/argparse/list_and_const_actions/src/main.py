# argparse append + store_const actions (Phase 7D3).
# extend lands with nargs in Phase E -- without nargs CPython argparse
# iterates the converted value, with surprising semantics.
from tpy import Int32
from argparse import ArgumentParser


def main() -> Int32:
    parser = ArgumentParser()
    parser.add_argument("--tag", action="append")
    parser.add_argument("--num", action="append", type=int)
    parser.add_argument("--mode", action="store_const", const="fast", default="slow")
    args = parser.parse_args(
        ["--tag", "a", "--tag", "b", "--num", "1", "--num", "2", "--mode"]
    )
    assert args.tag is not None
    assert args.num is not None
    print(args.tag[0])
    print(args.tag[1])
    print(args.num[0])
    print(args.num[1])
    print(args.mode)
    return 0


main()
