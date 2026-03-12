# Tests that Span[readonly[T]] cannot be used to construct a mutable Span
from tpy import Int32, Span, readonly

def main() -> None:
    lst: list[Int32] = [1, 2, 3]
    ros: Span[readonly[Int32]] = Span[readonly[Int32]](lst)
    s: Span[Int32] = Span[Int32](ros)  # tpyc: error(/cannot be constructed/)

main()
