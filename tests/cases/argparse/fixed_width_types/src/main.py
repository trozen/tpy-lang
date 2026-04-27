# Fixed-width int / uint in type= (T1.B). The synthesized parse
# function calls the corresponding constructor (Int32(...), etc.)
# on argv tokens, so the resulting record fields carry the matching
# primitive type. Float32 isn't supported yet -- see the comment on
# _ALLOWED_TYPES in lib/tpy/argparse.py.
from tpy import Int32, Int64, UInt16
from argparse import ArgumentParser


def main() -> Int32:
    parser = ArgumentParser()
    parser.add_argument("--width", type=Int32)
    parser.add_argument("--height", type=UInt16, default=64)
    parser.add_argument("--depth", type=Int64)
    args = parser.parse_args(["--width", "42", "--depth", "9999999999"])
    assert args.width is not None
    assert args.depth is not None
    print(args.width)
    print(args.height)
    print(args.depth)
    return 0


main()
