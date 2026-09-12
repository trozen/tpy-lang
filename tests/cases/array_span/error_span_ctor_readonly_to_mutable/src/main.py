# Tests that Span[readonly[T]] cannot be used to construct a mutable Span
from tpy import int32, Span, readonly

def main() -> None:
    lst: list[int32] = [1, 2, 3]
    ros: Span[readonly[int32]] = Span[readonly[int32]](lst)
    s: Span[int32] = Span[int32](ros)  # tpyc: error(/cannot be constructed/)

main()
