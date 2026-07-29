# An out-of-range integer literal as a parameter default is rejected the same
# way it is in an assignment -- unchecked it wrapped silently to -56.
from tpy import Int8


def offset(x: Int8 = 200) -> Int8:  # tpyc: error(/200 is outside Int8 range/)
    return x


def main() -> None:
    print(offset())


main()
