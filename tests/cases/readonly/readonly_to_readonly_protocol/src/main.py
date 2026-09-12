# readonly[list[T]] can be passed to Sized and Sequence (readonly protocols).
from tpy import int32, readonly
from typing import Sized, Sequence

def get_len(s: Sized) -> int32:
    return int32(len(s))

def get_first(s: Sequence[int32]) -> int32:
    return s[0]

def observe(items: readonly[list[int32]]) -> None:
    print(get_len(items))
    print(get_first(items))

def main() -> None:
    xs: list[int32] = [int32(10), int32(20), int32(30)]
    observe(xs)

main()
