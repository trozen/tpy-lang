from tpy import Int32, Span, Array

def sum_span(values: Span[Int32]) -> Int32:
    total: Int32 = 0
    i: Int32 = 0
    while i < len(values):
        total += values[i]
        i += 1
    return total

# Test 1: Array literal passed directly to Span parameter
print(sum_span([1, 2, 3, 4, 5]))

# Test 2: Array literal assigned to variable with explicit type, then passed
nums: Array[Int32, 3] = [10, 20, 30]
print(sum_span(nums))

# Test 3: Array with explicit type annotation
arr: Array[Int32, 3] = [100, 200, 300]
print(sum_span(arr))

# Test 4: list -> Span conversion
items: list[Int32] = [1000, 2000, 3000, 4000]
print(sum_span(items))
