from tpy import Int32, Span, copy

def get_span(data: list[Int32]) -> Span[Int32]:
    return data

def main() -> None:
    nums: list[Int32] = [Int32(1), Int32(2), Int32(3)]
    span: Span[Int32] = get_span(nums)

    # copy() on a Span should work (creates a view)
    span_copy: Span[Int32] = copy(span)

    # Both spans can access the same data
    print(span[0])
    print(span_copy[0])
    print(len(span))
    print(len(span_copy))

main()
