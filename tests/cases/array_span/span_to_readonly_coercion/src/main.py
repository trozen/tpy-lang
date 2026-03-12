# Test that Span[T] auto-coerces to Span[readonly[T]] when passed as argument.
from tpy import Int32, Span, Array, readonly

def sum_span(s: Span[readonly[Int32]]) -> Int32:
    total: Int32 = 0
    for i in range(len(s)):
        total += s[i]
    return total

def main() -> None:
    arr = Array[Int32, 3]([10, 20, 30])
    # Array coerces to Span, then Span coerces to Span[readonly[...]]
    result = sum_span(arr)
    print(result)

main()
