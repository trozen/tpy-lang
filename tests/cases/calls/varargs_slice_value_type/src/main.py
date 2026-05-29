# Regression guard: `args[1:]` on a value-type *args still works through the
# generic `list_slice` template (value-type varargs has `data()`). The new
# non-value list_slice overload is gated on `varargs<T, false>` so it must
# not be selected here.
from tpy import Int32


def sum_tail(*nums: Int32) -> Int32:  # tpyc: ok
    n: Int32 = 0
    for x in nums[1:]:
        n += x
    return n


def main() -> None:
    print(sum_tail(10, 20, 30, 40))  # 20+30+40 = 90


main()
