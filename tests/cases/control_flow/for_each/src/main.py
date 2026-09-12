from tpy import int32, Span, Array

# Test for-each over inferred list (no annotation needed)
items = [1, 2, 3, 4, 5]
total: int32 = 0
for x in items:
    total += x
print(total)  # 15

# Test for-each over Array
arr: Array[int32, 3] = [10, 20, 30]
for val in arr:
    print(val)

# Test for-each with local inferred array (no mutation -> std::array)
def sum_array() -> int32:
    nums = [100, 200, 300]
    result: int32 = 0
    for n in nums:
        result += n
    return result

print(sum_array())  # 600

# Test for-each over Span parameter
def print_span(data: Span[int32]) -> None:
    for x in data:
        print(x)

print_span([7, 8, 9])

# Test for-each over string
text = "AB"
for c in text:
    print(c)

# Test nested for-each
def nested_sum() -> int32:
    outer = [1, 2]
    inner = [10, 20]
    total: int32 = 0
    for a in outer:
        for b in inner:
            total += a * b
    return total

print(nested_sum())  # 1*10 + 1*20 + 2*10 + 2*20 = 90
