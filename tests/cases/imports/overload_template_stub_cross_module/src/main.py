# Cross-module call of a template (protocol-param) overload: guards that
# agg.total's Iterable[Own[int32]] overload instantiates here (header, not .cpp).
from agg import total
from tpy import int32


def main() -> None:
    nums: list[int32] = [3, 1, 2]
    print(total(nums))


main()
