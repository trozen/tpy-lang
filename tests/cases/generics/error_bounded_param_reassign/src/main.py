# A ValueType-bounded parameter cannot be reassigned, as an unbounded one cannot
# (BUGS.md#generic-param-reassign-rejected).
from tpy import ValueType


def pick[T: ValueType](x: T, y: T) -> T:
    x = y  # tpyc: error(/Cannot reassign parameter 'x' of type 'T'/)
    return x
