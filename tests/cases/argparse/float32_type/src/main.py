# float32 in argparse type=. The synthesized parse function lowers
# float32(argv[i]) to tpy::float32_from_str(...) so the record field
# carries a 32-bit float (not double). Values below are exact in
# IEEE-754 single precision so output matches CPython's `float` stub.
from tpy import float32
from argparse import ArgumentParser


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument("scale", type=float32)
    parser.add_argument("--bias", type=float32, default=0.5)
    parser.add_argument("--gain", type=float32)
    args = parser.parse_args(["2.5", "--gain", "1.25"])
    assert args.gain is not None
    print(args.scale)
    print(args.bias)
    print(args.gain)


main()
