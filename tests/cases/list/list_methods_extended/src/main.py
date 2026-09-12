from tpy import int32

# Test extended list methods: pop(index), index(), count(), reverse(), copy(), __setitem__

def print_list(nums: list[int32]) -> None:
    i: int32 = 0
    while i < len(nums):
        print(nums[i])
        i += 1
    print("---")

# === list[T] methods ===

def test_pop_at_index() -> None:
    nums: list[int32] = [10, 20, 30, 40, 50]

    # Pop from middle
    val: int32 = nums.pop(2)
    print(val)
    print_list(nums)

    # Pop from beginning
    val = nums.pop(0)
    print(val)
    print_list(nums)

    # Pop with negative index (-1 = last)
    val = nums.pop(-1)
    print(val)
    print_list(nums)

def test_index() -> None:
    nums: list[int32] = [10, 20, 30, 20, 40]

    print(nums.index(10))  # 0
    print(nums.index(20))  # 1 (first occurrence)
    print(nums.index(30))  # 2
    print(nums.index(40))  # 4

def test_count() -> None:
    nums: list[int32] = [1, 2, 2, 3, 2, 4, 2]

    print(nums.count(1))  # 1
    print(nums.count(2))  # 4
    print(nums.count(3))  # 1
    print(nums.count(5))  # 0 (not found)

def test_reverse() -> None:
    nums: list[int32] = [1, 2, 3, 4, 5]
    nums.reverse()
    print_list(nums)

    # Reverse again
    nums.reverse()
    print_list(nums)

def test_copy() -> None:
    nums: list[int32] = [1, 2, 3]
    copy: list[int32] = nums.copy()

    # Modify original
    nums.append(4)

    # Copy should be unaffected
    print(len(nums))   # 4
    print(len(copy))   # 3
    print_list(copy)

def test_setitem() -> None:
    nums: list[int32] = [10, 20, 30]

    nums[0] = 100
    nums[2] = 300
    print_list(nums)

    # Negative index
    nums[-1] = 999
    print_list(nums)

# Run all tests
print("=== list pop(index) ===")
test_pop_at_index()
print("=== list index ===")
test_index()
print("=== list count ===")
test_count()
print("=== list reverse ===")
test_reverse()
print("=== list copy ===")
test_copy()
print("=== list setitem ===")
test_setitem()
