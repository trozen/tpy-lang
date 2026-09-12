# Generic union T | U | str with T == U produces partial duplicate members
from tpy import int32


def pick[T, U](x: T, y: U) -> T | U | str:
    return x


pick[int32, int32](int32(1), int32(2))  # tpyc: error(/duplicate members/)
