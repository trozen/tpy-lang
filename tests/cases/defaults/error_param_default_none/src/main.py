# A `None` default on a non-Optional parameter is a type error, not a null
# initializer -- unchecked it emitted `int32_t n = nullptr`.
from tpy import Int32


def scaled(n: Int32 = None) -> Int32:  # tpyc: error(/expected Int32, got None/)
    return n * 2


def main() -> None:
    print(scaled())


main()
