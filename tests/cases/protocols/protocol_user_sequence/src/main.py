from typing import Sequence
from tpy import Int32

class IntWrapper:
    data: list[Int32]

    def __init__(self, items: list[Int32]) -> None:
        self.data = items

    def __len__(self) -> Int32:
        return len(self.data)

    def __getitem__(self, index: Int32) -> Int32:
        return self.data[index]

def sum_seq(s: Sequence[Int32]) -> Int32:
    total: Int32 = 0
    i: Int32 = 0
    while i < len(s):
        total += s[i]
        i += 1
    return total

def first(s: Sequence[Int32]) -> Int32:
    return s[0]

def main() -> None:
    nums: list[Int32] = [10, 20, 30, 40]
    wrapper: IntWrapper = IntWrapper(nums)

    # Direct indexing on user record
    print(wrapper[0])      # 10
    print(wrapper[-1])     # 40

    # User record conforms to Sequence[Int32]
    print(sum_seq(wrapper))  # 100
    print(first(wrapper))    # 10

    # Same functions work with regular list
    print(sum_seq(nums))     # 100
    print(first(nums))       # 10

main()
