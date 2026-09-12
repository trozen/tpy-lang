# Generic union T | U with T == U produces duplicate variant members
from tpy import int32


def pick[T, U](x: T, y: U) -> T | U:
    return x


pick[int32, int32](int32(1), int32(2))  # tpyc: error(/duplicate members/)
