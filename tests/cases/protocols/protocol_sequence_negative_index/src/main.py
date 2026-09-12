# Negative indexing through protocol-typed Sequence uses bounds-checked
# tpy::__getitem__ (not bare operator[]).
from tpy import int32, Array, Span
from typing import Sequence

def seq_at(s: Sequence[int32], i: int32) -> int32:
    return s[i]

def seq_str_at(s: Sequence[str], i: int32) -> str:
    return s[i]

def main() -> None:
    a: Array[int32, 3] = [int32(10), int32(20), int32(30)]
    print(seq_at(a, int32(-1)))
    print(seq_at(a, int32(0)))

    sp: Span[int32] = a
    print(seq_at(sp, int32(-1)))
    print(seq_at(sp, int32(-2)))

main()
