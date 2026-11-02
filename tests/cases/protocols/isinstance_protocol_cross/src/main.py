# isinstance() checking a different protocol than the declared one
from typing import Sequence
from tpy import int32, Hashable

def cross_protocol(items: Sequence[int32]) -> None:
    # the warning is right: Hashable is undecorated because CPython's isinstance raises against it
    if isinstance(items, Hashable):  # tpyc: warning(/TPy-only/)
        print("hashable sequence of", len(items))
    else:
        print("non-hashable sequence of", len(items))

def main() -> None:
    nums: list[int32] = [10, 20, 30]
    cross_protocol(nums)

main()
