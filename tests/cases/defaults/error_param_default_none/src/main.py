# A `None` default on a non-Optional parameter is a type error, not a null
# initializer -- unchecked it emitted `int32_t n = nullptr`.
from tpy import int32


def scaled(n: int32 = None) -> int32:  # tpyc: error(/expected int32, got None/)
    return n * 2


def main() -> None:
    print(scaled())


main()
