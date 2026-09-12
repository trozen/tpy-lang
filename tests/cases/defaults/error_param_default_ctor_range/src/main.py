# A fixed-int constructor default carries its own range contract: the wrapped
# literal is checked against the constructor's type before that type is
# checked against the parameter's.
from tpy import int8


def offset(x: int8 = int8(200)) -> int8:  # tpyc: error(/200 is outside int8 range/)
    return x


def main() -> None:
    print(offset())


main()
