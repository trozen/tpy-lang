# Cross-module call of a template (protocol-param) overload: guards that
# agg.total's Iterable[Own[Int32]] overload instantiates here (header, not .cpp).
from agg import total
from tpy import Int32


def main() -> None:
    nums: list[Int32] = [3, 1, 2]
    print(total(nums))


main()
