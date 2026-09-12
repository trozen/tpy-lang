"""Tests that str does NOT conform to Iterable[int32] (it is Iterable[char])."""
from typing import Iterable
from tpy import int32

def sum_ints(items: Iterable[int32]) -> int32:
    total: int32 = 0
    for x in items:
        total += x
    return total

def main() -> None:
    s: str = "hello"
    sum_ints(s)  # tpyc: error(/does not conform to protocol/)

main()
