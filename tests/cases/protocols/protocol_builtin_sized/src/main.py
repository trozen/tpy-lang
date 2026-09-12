from typing import Sized
from tpy import int32

def count(items: Sized) -> int32:
    return len(items)

def main() -> None:
    nums: list[int32] = [1, 2, 3]
    print(count(nums))

main()
