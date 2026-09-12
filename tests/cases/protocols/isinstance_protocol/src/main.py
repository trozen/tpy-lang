# isinstance() on static protocols -- compile-time check via if constexpr
from typing import Sized
from tpy import int32

def describe(items: Sized) -> None:
    if isinstance(items, Sized):
        print("sized:", len(items))
    else:
        print("not sized")

def check_not(items: Sized) -> None:
    if not isinstance(items, Sized):
        print("not sized")
    else:
        print("sized:", len(items))

def main() -> None:
    nums: list[int32] = [10, 20, 30]
    describe(nums)
    check_not(nums)

main()
