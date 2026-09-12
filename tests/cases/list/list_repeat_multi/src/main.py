from tpy import int32

# Multi-element list repetition
repeated: list[int32] = [1, 2] * 3
print(len(repeated))
for i in range(len(repeated)):
    print(repeated[i])

# Multi-element with list
nums: list[int32] = [10, 20] * 2
print(len(nums))
for i in range(len(nums)):
    print(nums[i])

# Empty list repetition (always produces empty list)
empty: list[int32] = [] * 100
print(len(empty))

# Negative repeat count (Python semantics: produces empty list)
neg: list[int32] = [1, 2, 3] * -5
print(len(neg))
