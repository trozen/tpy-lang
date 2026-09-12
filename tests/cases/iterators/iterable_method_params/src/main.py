# Test Iterable[T] as param type for extend, join, list() constructors
from typing import Iterable
from tpy import int32, Own

def extend_from(target: list[int32], items: Iterable[int32]) -> None:
    target.extend(items)

def join_from(sep: str, items: Iterable[str]) -> str:
    return sep.join(items)

def list_from(items: Iterable[int32]) -> Own[list[int32]]:
    return list(items)

def main() -> None:
    # list.extend with Iterable param
    nums: list[int32] = [1, 2]
    more: list[int32] = [3, 4, 5]
    extend_from(nums, more)
    print(nums)

    # list.extend with list literal
    nums2: list[int32] = [10]
    extend_from(nums2, [20, 30])
    print(nums2)

    # list.extend with another list via Iterable
    target: list[int32] = [100]
    vals: list[int32] = [200, 300]
    extend_from(target, vals)
    print(target)

    # str.join with Iterable param
    words: list[str] = ["a", "b", "c"]
    print(join_from("-", words))

    # list() from Iterable param
    src: list[int32] = [7, 8, 9]
    result = list_from(src)
    print(result)

    # Direct calls (not through Iterable param)
    direct: list[int32] = [1]
    direct.extend([2, 3])
    print(direct)

    print(",".join(["x", "y"]))

main()
