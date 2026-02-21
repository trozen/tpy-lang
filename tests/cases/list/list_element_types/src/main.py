from tpy import Int32, Span

# Case 1: Pure literals (BigInt by default)
pure_literals = [1, 2, 3]
print(pure_literals[0])

# Case 2: Int32 constructor in list forces Int32 element type
mixed_int32 = [Int32(1), 2, 3]
print(mixed_int32[0])

# Case 3: Literal first, then Int32 - should infer Int32
mixed_int32_rev = [1, Int32(2), 3]
print(mixed_int32_rev[1])

# Case 4: Explicit list[Int32] annotation
annotated_int32: list[Int32] = [10, 20, 30]
print(annotated_int32[0])

# Case 5: Explicit list[int] annotation (BigInt)
annotated_bigint: list[int] = [100, 200, 300]
print(annotated_bigint[0])

# Case 6: Local variable with Int32 element
def local_mixed() -> Int32:
    data = [Int32(5), 6, 7]
    return data[2]

print(local_mixed())

# Case 7: Passing to Span[Int32] param requires Int32 elements
def sum_span(nums: Span[Int32]) -> Int32:
    total: Int32 = 0
    for n in nums:
        total += n
    return total

# Must annotate or use Int32 constructor for Span[Int32] compatibility
global_for_span: list[Int32] = [1, 2, 3]
print(sum_span(global_for_span))

# Case 8: Local list passed to Span[Int32] infers Int32
def test_local_span() -> Int32:
    local_data = [4, 5, 6]  # Inferred as Int32 when passed to Span[Int32]
    return sum_span(local_data)

print(test_local_span())

# Case 9: Span[int] (BigInt span)
def sum_span_bigint(nums: Span[int]) -> int:
    total: int = 0
    for n in nums:
        total += n
    return total

bigint_list = [1000, 2000, 3000]
print(sum_span_bigint(bigint_list))
