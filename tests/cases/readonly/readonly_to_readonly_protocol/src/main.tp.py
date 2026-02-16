# readonly[list[T]] can be passed to Sized and Sequence (readonly protocols).
from tpy import Int32, readonly
from typing import Sized, Sequence

def get_len(s: Sized) -> Int32:
    return Int32(len(s))

def get_first(s: Sequence[Int32]) -> Int32:
    return s[0]

def observe(items: readonly[list[Int32]]) -> None:
    print(get_len(items))
    print(get_first(items))

def main() -> None:
    xs: list[Int32] = [Int32(10), Int32(20), Int32(30)]
    observe(xs)

main()
