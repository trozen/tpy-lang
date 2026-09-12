# Fixed-width int / uint in type= (T1.B). The synthesized parse
# function calls the corresponding constructor (int32(...), etc.)
# on argv tokens, so the resulting record fields carry the matching
# primitive type. float32 is covered by float32_type/.
from tpy import int32, int64, uint16
from argparse import ArgumentParser


def main() -> int32:
    parser = ArgumentParser()
    parser.add_argument("--width", type=int32)
    parser.add_argument("--height", type=uint16, default=64)
    parser.add_argument("--depth", type=int64)
    args = parser.parse_args(["--width", "42", "--depth", "9999999999"])
    assert args.width is not None
    assert args.depth is not None
    print(args.width)
    print(args.height)
    print(args.depth)
    return 0


main()
