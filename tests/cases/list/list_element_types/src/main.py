from tpy import int32, Span

# Case 1: Pure literals (BigInt by default)
pure_literals = [1, 2, 3]
print(pure_literals[0])

# Case 2: int32 constructor in list forces int32 element type
mixed_int32 = [int32(1), 2, 3]
print(mixed_int32[0])

# Case 3: Literal first, then int32 - should infer int32
mixed_int32_rev = [1, int32(2), 3]
print(mixed_int32_rev[1])

# Case 4: Explicit list[int32] annotation
annotated_int32: list[int32] = [10, 20, 30]
print(annotated_int32[0])

# Case 5: Explicit list[int] annotation (BigInt)
annotated_bigint: list[int] = [100, 200, 300]
print(annotated_bigint[0])

# Case 6: Local variable with int32 element
def local_mixed() -> int32:
    data = [int32(5), 6, 7]
    return data[2]

print(local_mixed())

# Case 7: Passing to Span[int32] param requires int32 elements
def sum_span(nums: Span[int32]) -> int32:
    total: int32 = 0
    for n in nums:
        total += n
    return total

# Must annotate or use int32 constructor for Span[int32] compatibility
global_for_span: list[int32] = [1, 2, 3]
print(sum_span(global_for_span))

# Case 8: Local list passed to Span[int32] infers int32
def test_local_span() -> int32:
    local_data = [4, 5, 6]  # Inferred as int32 when passed to Span[int32]
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
