# A Final constant read off a module (`consts.LABEL`) as a default is
# type-checked against its parameter, as a bare Final name is.
from tpy import int32
import consts


def scaled(n: int32 = consts.LABEL) -> int32:  # tpyc: error(/expected int32, got StrView/)
    return n * 2


def main() -> None:
    print(scaled())


main()
