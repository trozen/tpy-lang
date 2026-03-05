# Tests that ReadOnlySpan cannot be used to construct a mutable Span
from tpy import Int32, ReadOnlySpan, Span

def main() -> None:
    lst: list[Int32] = [1, 2, 3]
    ros: ReadOnlySpan[Int32] = ReadOnlySpan[Int32](lst)
    s: Span[Int32] = Span[Int32](ros)  # tpyc: error(/cannot be constructed/)

main()
