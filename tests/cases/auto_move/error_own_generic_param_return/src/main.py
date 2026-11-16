# An Own[T] param of a generic body is callee storage at the return, as at
# the concrete twin: an unbounded T | None return would point into it at a
# reference instantiation and dangle (a ValueType-bounded one returns
# `std::optional<T>`, see own_generic_param_borrow_return). Its by-value
# spelling `-> Own[T | None]` does not lower yet at a value instantiation
# (BUGS.md#own-generic-param-optional-lever).
from tpy import Own


def opt_any[T](x: Own[T]) -> T | None:
    return x  # tpyc: error(/Cannot return local or temporary as 'T \| None'/)


def main() -> None:
    pass


main()
