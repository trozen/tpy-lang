# Generic union T | U | str with T == U produces partial duplicate members
from tpy import Int32


def pick[T, U](x: T, y: U) -> T | U | str:
    return x


pick[Int32, Int32](Int32(1), Int32(2))  # tpyc: error(/duplicate members/)
