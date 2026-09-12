# Span[readonly[T]] cannot coerce to mutable Span[T].
from tpy import int32, Span, Array, readonly

def take_mutable(s: Span[int32]) -> int32:
    return s[0]

def main() -> None:
    arr: Array[int32, 3] = [1, 2, 3]
    rs: Span[readonly[int32]] = arr
    take_mutable(rs)  # tpyc: error(/expected Span\[int32\], got Span\[readonly\[int32\]\]/)

main()
