from typing import Sized
from tpy import Int32

def count(items: Sized) -> Int32:
    return len(items)

def main() -> None:
    nums: list[Int32] = [1, 2, 3]
    print(count(nums))

main()
