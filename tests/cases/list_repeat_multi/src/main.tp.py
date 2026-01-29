from tpy import Int32, StaticList

# Multi-element list repetition with StaticList
sl: StaticList[Int32, 8] = StaticList[Int32, 8]([1, 2] * 3)
print(len(sl))
for i in range(len(sl)):
    print(sl[i])

# Multi-element with std::vector (list)
nums: list[Int32] = [10, 20] * 2
print(len(nums))
for i in range(len(nums)):
    print(nums[i])

# Empty list repetition (always produces empty list)
empty: list[Int32] = [] * 100
print(len(empty))
