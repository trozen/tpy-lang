from tpy import int32, Span, Array

# Returning Span should return by value (std::span is a view type)
def get_span(arr: Array[int32, 4]) -> Span[int32]:
    return arr  # tpyc: ok

def main():
    nums: Array[int32, 4] = [1, 2, 3, 4]
    s: Span[int32] = get_span(nums)
    print(s[0])
    print(s[3])

main()
