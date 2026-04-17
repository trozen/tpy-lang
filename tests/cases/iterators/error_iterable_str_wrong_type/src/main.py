"""Tests that str does NOT conform to Iterable[Int32] (it is Iterable[Char])."""
from typing import Iterable
from tpy import Int32

def sum_ints(items: Iterable[Int32]) -> Int32:
    total: Int32 = 0
    for x in items:
        total += x
    return total

def main() -> None:
    s: str = "hello"
    sum_ints(s)  # tpyc: error(/does not conform to protocol/)

main()
