# A fixed-int constructor default carries its own range contract: the wrapped
# literal is checked against the constructor's type before that type is
# checked against the parameter's.
from tpy import Int8


def offset(x: Int8 = Int8(200)) -> Int8:  # tpyc: error(/200 is outside Int8 range/)
    return x


def main() -> None:
    print(offset())


main()
