from tpy import int32, Span, copy

def get_span(data: list[int32]) -> Span[int32]:
    return data

def main() -> None:
    nums: list[int32] = [int32(1), int32(2), int32(3)]
    span: Span[int32] = get_span(nums)

    # copy() on a Span should work (creates a view)
    span_copy: Span[int32] = copy(span)

    # Both spans can access the same data
    print(span[0])
    print(span_copy[0])
    print(len(span))
    print(len(span_copy))

main()
