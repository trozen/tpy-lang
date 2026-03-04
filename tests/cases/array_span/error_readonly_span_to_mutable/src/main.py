# ReadOnlySpan[T] cannot coerce to mutable Span[T].
from tpy import Int32, Span, ReadOnlySpan, Array

def take_mutable(s: Span[Int32]) -> Int32:
    return s[0]

def main() -> None:
    arr: Array[Int32, 3] = [1, 2, 3]
    rs: ReadOnlySpan[Int32] = arr
    take_mutable(rs)  # tpyc: error(/expected Span\[Int32\], got ReadOnlySpan\[Int32\]/)

main()
