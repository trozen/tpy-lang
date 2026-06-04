# Regression guard: `args[1:]` on a value-type *args returns a varargs[Int32]
# (via the value-type `varargs<T, true>` list_slice overload), so the slice
# stays a body view rather than degrading to a std::span.
from tpy import Int32


def sum_tail(*nums: Int32) -> Int32:  # tpyc: ok
    n: Int32 = 0
    for x in nums[1:]:
        n += x
    return n


def main() -> None:
    print(sum_tail(10, 20, 30, 40))  # 20+30+40 = 90


main()
