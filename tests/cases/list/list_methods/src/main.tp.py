from tpy import Int32, Array, StaticList

# Test all list methods: pop(), insert(), remove(), clear(), extend()

def print_list(nums: list[Int32]) -> None:
    """Helper to print list contents."""
    i: Int32 = 0
    while i < len(nums):
        print(nums[i])
        i += 1
    print("---")

def test_pop() -> None:
    """Test pop() - remove and return last element."""
    nums: list[Int32] = [10, 20, 30, 40]

    # Pop last element
    last: Int32 = nums.pop()
    print(last)
    print(len(nums))

    # Pop again
    second_last: Int32 = nums.pop()
    print(second_last)
    print(len(nums))

    print_list(nums)

def test_insert() -> None:
    """Test insert(index, value) - insert at specific position."""
    nums: list[Int32] = [10, 30, 40]

    # Insert at beginning
    nums.insert(0, 5)
    print_list(nums)

    # Insert in middle
    nums.insert(2, 20)
    print_list(nums)

    # Insert at end (same as append)
    nums.insert(5, 50)
    print_list(nums)

def test_remove() -> None:
    """Test remove(value) - remove first occurrence of value."""
    nums: list[Int32] = [10, 20, 30, 20, 40]

    # Remove first occurrence of 20
    nums.remove(20)
    print_list(nums)

    # Remove 10
    nums.remove(10)
    print_list(nums)

    # Remove 40
    nums.remove(40)
    print_list(nums)

def test_clear() -> None:
    """Test clear() - remove all elements."""
    nums: list[Int32] = [1, 2, 3, 4, 5]
    print(len(nums))

    nums.clear()
    print(len(nums))

    # Can still append after clear
    nums.append(100)
    print(len(nums))
    print(nums[0])

def test_extend() -> None:
    """Test extend(iterable) - add all elements from another collection."""
    nums: list[Int32] = [1, 2, 3]

    # Extend with array literal
    nums.extend([4, 5, 6])
    print_list(nums)

    # Extend with another list
    more: list[Int32] = [7, 8]
    nums.extend(more)
    print_list(nums)

    # Extend with Array variable
    arr: Array[Int32, 2] = [9, 10]
    nums.extend(arr)
    print_list(nums)

    # Extend with StaticList
    sl: StaticList[Int32, 3] = StaticList[Int32, 3]()
    sl.append(11)
    sl.append(12)
    nums.extend(sl)
    print_list(nums)

def test_combined_operations() -> None:
    """Test combining multiple list methods."""
    nums: list[Int32] = [5]

    nums.append(10)
    nums.insert(0, 1)
    nums.extend([15, 20])
    print_list(nums)

    nums.remove(10)
    print_list(nums)

    popped: Int32 = nums.pop()
    print(popped)
    print_list(nums)

    nums.clear()
    print(len(nums))

# Run all tests
print("=== pop ===")
test_pop()
print("=== insert ===")
test_insert()
print("=== remove ===")
test_remove()
print("=== clear ===")
test_clear()
print("=== extend ===")
test_extend()
print("=== combined ===")
test_combined_operations()
