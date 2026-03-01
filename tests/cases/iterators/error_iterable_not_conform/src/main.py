# Error: passing non-iterable type to Iterable[Int32] parameter
from typing import Iterable
from tpy import Int32

class NotIterable:
    value: Int32

    def __init__(self, v: Int32) -> None:
        self.value = v

def sum_items(items: Iterable[Int32]) -> Int32:  # tpyc: ok
    total: Int32 = 0
    for x in items:
        total += x
    return total

def main() -> None:
    n = NotIterable(42)
    print(sum_items(n))  # tpyc: error(/does not conform to protocol/)

main()
