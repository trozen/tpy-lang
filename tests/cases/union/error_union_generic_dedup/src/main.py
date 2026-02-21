# Generic union T | U with T == U produces duplicate variant members
from tpy import Int32


def pick[T, U](x: T, y: U) -> T | U:
    return x


pick[Int32, Int32](Int32(1), Int32(2))  # tpyc: error(/duplicate members/)
