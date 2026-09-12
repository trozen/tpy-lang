# Error: passing non-iterable type to Iterable[int32] parameter
from typing import Iterable
from tpy import int32

class NotIterable:
    value: int32

    def __init__(self, v: int32) -> None:
        self.value = v

def sum_items(items: Iterable[int32]) -> int32:
    total: int32 = 0
    for x in items:
        total += x
    return total

def main() -> None:
    n = NotIterable(42)
    print(sum_items(n))  # tpyc: error(/does not conform to protocol/)

main()
