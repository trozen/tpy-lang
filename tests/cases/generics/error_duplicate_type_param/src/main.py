# A type parameter name declared twice is refused.
from tpy import int32


def f[T, T](x: T) -> int32:  # tpyc: error(/Function 'f': duplicate type parameter name 'T'/)
    return 1
