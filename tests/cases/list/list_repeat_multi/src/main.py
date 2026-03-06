from tpy import Int32

# Multi-element list repetition
repeated: list[Int32] = [1, 2] * 3
print(len(repeated))
for i in range(len(repeated)):
    print(repeated[i])

# Multi-element with list
nums: list[Int32] = [10, 20] * 2
print(len(nums))
for i in range(len(nums)):
    print(nums[i])

# Empty list repetition (always produces empty list)
empty: list[Int32] = [] * 100
print(len(empty))

# Negative repeat count (Python semantics: produces empty list)
neg: list[Int32] = [1, 2, 3] * -5
print(len(neg))
