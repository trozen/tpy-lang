"""Tests that str conforms to NativeIterable[Char] via extends declaration."""
from tpy import Int32, Char, NativeIterable, Span

def count_chars(text: NativeIterable[Char]) -> Int32:
    """Function accepting NativeIterable[Char] - str should work."""
    count: Int32 = 0
    for c in text:
        count += 1
    return count

def first_char(text: NativeIterable[Char]) -> Char:
    """Get first character via iteration."""
    for c in text:
        return c
    return chr(0)

def sum_span(items: NativeIterable[Int32]) -> Int32:
    """Test that Span also conforms to NativeIterable via extends."""
    total: Int32 = 0
    for x in items:
        total += x
    return total

def main() -> None:
    # str variable passed to NativeIterable[Char] param
    s: str = "hello"
    print(count_chars(s))  # 5
    print(first_char(s))   # h

    # Another str variable
    abc: str = "abc"
    print(count_chars(abc))  # 3

    # Span with NativeIterable (via extends)
    nums: list[Int32] = [10, 20, 30]
    sp: Span[Int32] = nums
    print(sum_span(sp))  # 60

main()
