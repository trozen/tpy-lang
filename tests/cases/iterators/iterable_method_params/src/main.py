# Test Iterable[T] as param type for extend, join, list() constructors
from typing import Iterable
from tpy import Int32, Own, StaticList

def extend_from(target: list[Int32], items: Iterable[Int32]) -> None:
    target.extend(items)

def sl_extend_from(target: StaticList[Int32, 16], items: Iterable[Int32]) -> None:
    target.extend(items)

def join_from(sep: str, items: Iterable[str]) -> str:
    return sep.join(items)

def list_from(items: Iterable[Int32]) -> Own[list[Int32]]:
    return list(items)

def main() -> None:
    # list.extend with Iterable param
    nums: list[Int32] = [1, 2]
    more: list[Int32] = [3, 4, 5]
    extend_from(nums, more)
    print(nums)

    # list.extend with list literal
    nums2: list[Int32] = [10]
    extend_from(nums2, [20, 30])
    print(nums2)

    # StaticList.extend with Iterable param
    sl: StaticList[Int32, 16] = StaticList[Int32, 16]()
    sl.append(100)
    vals: list[Int32] = [200, 300]
    sl_extend_from(sl, vals)
    print(sl)

    # str.join with Iterable param
    words: list[str] = ["a", "b", "c"]
    print(join_from("-", words))

    # list() from Iterable param
    src: list[Int32] = [7, 8, 9]
    result = list_from(src)
    print(result)

    # Direct calls (not through Iterable param)
    direct: list[Int32] = [1]
    direct.extend([2, 3])
    print(direct)

    print(",".join(["x", "y"]))

main()
