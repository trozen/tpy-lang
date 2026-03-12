# Span[readonly[T]] cannot coerce to mutable Span[T].
from tpy import Int32, Span, Array, readonly

def take_mutable(s: Span[Int32]) -> Int32:
    return s[0]

def main() -> None:
    arr: Array[Int32, 3] = [1, 2, 3]
    rs: Span[readonly[Int32]] = arr
    take_mutable(rs)  # tpyc: error(/expected Span\[Int32\], got Span\[readonly\[Int32\]\]/)

main()
