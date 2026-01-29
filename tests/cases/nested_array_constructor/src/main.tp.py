"""Tests nested array constructor with proper brace generation.

Array[Array[T, N], M]() should generate correct triple-brace initialization
for std::array<std::array<T, N>, M>.
"""
from tpy import Int32, Span, Array

def take_span_nested(s: Span[Array[Int32, 2]]) -> Int32:
    return s[0][0] + s[1][1]

def main() -> None:
    # Inline nested array constructor passed to Span parameter
    result: Int32 = take_span_nested(Array[Array[Int32, 2], 2]([[1, 2], [3, 4]]))
    print(result)  # 1 + 4 = 5

main()
