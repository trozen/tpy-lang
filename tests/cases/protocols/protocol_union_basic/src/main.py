# Required protocol union (2 protocols): isinstance dispatch
# on a param typed as a union of two static protocols.
from typing import Protocol, Sized, Sequence

def describe(items: Sized | Sequence[int]) -> None:
    if isinstance(items, Sequence):
        print(items[0])
    elif isinstance(items, Sized):
        print(len(items))

def get_value(items: Sized | Sequence[int]) -> int:
    if isinstance(items, Sequence):
        return items[0]
    elif isinstance(items, Sized):
        return len(items)
    return 0

def main() -> None:
    nums: list[int] = [10, 20, 30]
    # list satisfies both Sized and Sequence[int]; Sequence branch fires first
    describe(nums)
    print(get_value(nums))

main()
