# An Own[T] param of a generic body is callee storage at the return, as at
# the concrete twin: a T | None return would point into it and dangle, even
# under a ValueType bound (a function's own bound does not reach the
# return's form, BUGS.md#bounded-function-optional-return-pointer). Its
# by-value spelling `-> Own[T | None]` does not lower yet at a value
# instantiation (BUGS.md#own-generic-param-optional-lever).
from tpy import Own, ValueType


def opt_back[T: ValueType](x: Own[T]) -> T | None:
    return x  # tpyc: error(/Cannot return local or temporary as 'T \| None'/)


def main() -> None:
    pass


main()
