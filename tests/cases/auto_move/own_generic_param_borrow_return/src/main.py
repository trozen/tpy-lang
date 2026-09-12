# Generic Own[...T...] params keep the legacy params-are-safe treatment in
# the dangling-return check (the body is analyzed pre-instantiation, where
# a T return may be by-value): returning an element of an Own[list[T]] and
# returning a ValueType-bound Own[T] both stay accepted, unlike the
# concrete Own-param shapes (see error_own_param_borrow_return).
from tpy import int32, Own, ValueType


def first_val[T](items: Own[list[T]]) -> T:
    return items[0]  # tpyc: ok


def passthrough[T: ValueType](x: Own[T]) -> T:
    return x  # tpyc: ok


def main() -> None:
    nums: list[int32] = [10, 20, 30]
    print(first_val(nums))
    print(passthrough(7))


main()
