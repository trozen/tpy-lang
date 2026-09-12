# An out-of-range integer literal as a parameter default is rejected the same
# way it is in an assignment -- unchecked it wrapped silently to -56.
from tpy import int8


def offset(x: int8 = 200) -> int8:  # tpyc: error(/200 is outside int8 range/)
    return x


def main() -> None:
    print(offset())


main()
