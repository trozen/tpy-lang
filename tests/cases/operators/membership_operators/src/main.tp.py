from tpy import Int32, Array, Span

# Test 'in' and 'not in' operators for different container types

def test_list_membership() -> None:
    """Test membership for list[Int32]."""
    nums: list[Int32] = [10, 20, 30, 40, 50]

    # 'in' operator
    if 30 in nums:
        print("30 in list: yes")
    if 99 in nums:
        print("99 in list: yes")
    else:
        print("99 in list: no")

    # 'not in' operator
    if 99 not in nums:
        print("99 not in list: yes")
    if 30 not in nums:
        print("30 not in list: yes")
    else:
        print("30 not in list: no")

def test_array_membership() -> None:
    """Test membership for Array[Int32, N]."""
    arr: Array[Int32, 4] = [1, 2, 3, 4]

    if 3 in arr:
        print("3 in array: yes")
    if 5 in arr:
        print("5 in array: yes")
    else:
        print("5 in array: no")

    if 5 not in arr:
        print("5 not in array: yes")

def check_span_contains(data: Span[Int32], value: Int32) -> bool:
    """Test membership for Span[Int32]."""
    return value in data

def test_span_membership() -> None:
    """Test membership via Span parameter."""
    nums: Array[Int32, 5] = [100, 200, 300, 400, 500]

    if check_span_contains(nums, 300):
        print("300 in span: yes")
    if check_span_contains(nums, 999):
        print("999 in span: yes")
    else:
        print("999 in span: no")

def test_string_membership() -> None:
    """Test membership for str (character in string)."""
    text: str = "hello world"

    # Single character 'in' string
    if "o" in text:
        print("'o' in string: yes")
    if "z" in text:
        print("'z' in string: yes")
    else:
        print("'z' in string: no")

    # 'not in' for string
    if "z" not in text:
        print("'z' not in string: yes")
    if "e" not in text:
        print("'e' not in string: yes")
    else:
        print("'e' not in string: no")

def test_membership_in_conditions() -> None:
    """Test membership operators in complex conditions."""
    nums: list[Int32] = [1, 2, 3, 4, 5]

    # Combined with 'and'
    if 2 in nums and 4 in nums:
        print("both 2 and 4 in list")

    # Combined with 'or'
    if 10 in nums or 3 in nums:
        print("10 or 3 in list")

    # Negation combined
    if 1 in nums and 99 not in nums:
        print("1 in and 99 not in list")

def test_membership_with_variables() -> None:
    """Test membership with variable lookups."""
    nums: list[Int32] = [5, 10, 15, 20]
    target: Int32 = 10
    missing: Int32 = 7

    if target in nums:
        print("target found")
    if missing not in nums:
        print("missing not found")

# Run all tests
test_list_membership()
test_array_membership()
test_span_membership()
test_string_membership()
test_membership_in_conditions()
test_membership_with_variables()
