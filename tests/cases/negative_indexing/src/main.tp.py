from tpy import Int32, Array

# Test negative indexing for various container types
# Note: StaticList negative indexing is not yet implemented

def test_list_negative_indexing() -> None:
    """Test negative indexing on list."""
    nums: list[Int32] = [10, 20, 30, 40, 50]

    # Last element
    print(nums[-1])

    # Second to last
    print(nums[-2])

    # Third to last
    print(nums[-3])

    # First element via negative (should be same as nums[0])
    print(nums[-5])

def test_array_negative_indexing() -> None:
    """Test negative indexing on Array."""
    arr: Array[Int32, 4] = [100, 200, 300, 400]

    print(arr[-1])
    print(arr[-2])
    print(arr[-4])

def test_string_negative_indexing() -> None:
    """Test negative indexing on string."""
    text: str = "hello"

    # Last character
    print(text[-1])

    # Second to last
    print(text[-2])

    # First character via negative
    print(text[-5])

def test_negative_index_assignment() -> None:
    """Test assignment using negative index."""
    nums: list[Int32] = [1, 2, 3, 4, 5]

    # Modify last element
    nums[-1] = 50
    print(nums[-1])

    # Modify second to last
    nums[-2] = 40
    print(nums[-2])

    # Verify list contents
    print(nums[3])
    print(nums[4])

def test_negative_index_in_expression() -> None:
    """Test negative index used in expressions."""
    nums: list[Int32] = [5, 10, 15, 20]

    # Arithmetic with negative indexed values
    total: Int32 = nums[-1] + nums[-2]
    print(total)

    # Comparison with negative indexed values
    if nums[-1] > nums[-2]:
        print("last > second_last")
    else:
        print("last <= second_last")

def test_array_negative_assignment() -> None:
    """Test assignment using negative index on Array."""
    arr: Array[Int32, 3] = [1, 2, 3]

    arr[-1] = 30
    arr[-2] = 20
    arr[-3] = 10

    print(arr[0])
    print(arr[1])
    print(arr[2])

# Run all tests
print("=== list ===")
test_list_negative_indexing()
print("=== array ===")
test_array_negative_indexing()
print("=== string ===")
test_string_negative_indexing()
print("=== assignment ===")
test_negative_index_assignment()
print("=== expression ===")
test_negative_index_in_expression()
print("=== array assignment ===")
test_array_negative_assignment()
