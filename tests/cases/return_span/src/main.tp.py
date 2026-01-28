from tpy import Int32, Span, Array

# Returning Span should return by value (std::span is a view type)
def get_span(arr: Array[Int32, 4]) -> Span[Int32]:
    return arr  # tpyc: ok

def main():
    nums: Array[Int32, 4] = [1, 2, 3, 4]
    s: Span[Int32] = get_span(nums)
    print(s[0])
    print(s[3])

main()
