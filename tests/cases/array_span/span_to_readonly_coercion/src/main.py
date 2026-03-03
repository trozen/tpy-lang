# Test that Span[T] auto-coerces to ReadOnlySpan[T] when passed as argument.
from tpy import Int32, ReadOnlySpan, Array

def sum_span(s: ReadOnlySpan[Int32]) -> Int32:
    total: Int32 = 0
    for i in range(len(s)):
        total += s[i]
    return total

def main() -> None:
    arr = Array[Int32, 3]([10, 20, 30])
    # Array coerces to Span, then Span coerces to ReadOnlySpan
    result = sum_span(arr)
    print(result)

main()
