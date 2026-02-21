# Negative indexing through protocol-typed Sequence uses bounds-checked
# tpy::__getitem__ (not bare operator[]).
from tpy import Int32, Array, Span
from typing import Sequence

def seq_at(s: Sequence[Int32], i: Int32) -> Int32:
    return s[i]

def seq_str_at(s: Sequence[str], i: Int32) -> str:
    return s[i]

def main() -> None:
    a: Array[Int32, 3] = [Int32(10), Int32(20), Int32(30)]
    print(seq_at(a, Int32(-1)))
    print(seq_at(a, Int32(0)))

    sp: Span[Int32] = a
    print(seq_at(sp, Int32(-1)))
    print(seq_at(sp, Int32(-2)))

main()
