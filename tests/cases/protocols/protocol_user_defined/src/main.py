from typing import Protocol
from tpy import int32

class Measurable(Protocol):
    def __len__(self) -> int32: ...

def count(items: Measurable) -> int32:
    return len(items)

def main() -> None:
    nums: list[int32] = [1, 2, 3]
    print(count(nums))

main()
