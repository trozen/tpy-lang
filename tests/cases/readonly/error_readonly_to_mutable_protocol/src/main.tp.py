# readonly[T] can be passed to readonly protocols (Sized, Sequence) but not
# to MutableSequence.
from tpy import Int32, readonly
from typing import Sized, Sequence, MutableSequence

def read_len(s: Sized) -> Int32:
    return Int32(len(s))

def read_first(s: Sequence[Int32]) -> Int32:
    return s[0]

def mutate_first(xs: MutableSequence[Int32]) -> None:
    xs[0] = Int32(99)

def safe(items: readonly[list[Int32]]) -> Int32:
    n = read_len(items)  # tpyc: ok
    v = read_first(items)  # tpyc: ok
    return n + v

def bad(items: readonly[list[Int32]]) -> None:
    mutate_first(items)  # tpyc: error(/readonly/)
