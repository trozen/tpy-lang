from tpy import int32

# Test string comparison operators

def test_equality() -> None:
    """Test == and != for strings."""
    a: str = "hello"
    b: str = "hello"
    c: str = "world"

    # Equal strings
    if a == b:
        print("hello == hello: yes")
    else:
        print("hello == hello: no")

    # Unequal strings
    if a == c:
        print("hello == world: yes")
    else:
        print("hello == world: no")

    # Not equal
    if a != c:
        print("hello != world: yes")
    else:
        print("hello != world: no")

    if a != b:
        print("hello != hello: yes")
    else:
        print("hello != hello: no")

def test_ordering() -> None:
    """Test <, <=, >, >= for strings (lexicographic)."""
    a: str = "apple"
    b: str = "banana"
    c: str = "apple"

    # Less than
    if a < b:
        print("apple < banana: yes")

    if b < a:
        print("banana < apple: yes")
    else:
        print("banana < apple: no")

    # Less than or equal
    if a <= c:
        print("apple <= apple: yes")

    if a <= b:
        print("apple <= banana: yes")

    # Greater than
    if b > a:
        print("banana > apple: yes")

    # Greater than or equal
    if c >= a:
        print("apple >= apple: yes")

def test_empty_strings() -> None:
    """Test comparisons with empty strings."""
    empty: str = ""
    nonempty: str = "x"
    empty2: str = ""

    if empty == empty2:
        print("empty == empty: yes")

    if empty < nonempty:
        print("empty < x: yes")

    if empty != nonempty:
        print("empty != x: yes")

def strings_equal(s1: str, s2: str) -> bool:
    """Helper function to compare strings."""
    return s1 == s2

def test_comparison_in_function() -> None:
    """Test string comparison as function parameter/return."""
    if strings_equal("test", "test"):
        print("test == test: yes")

    if strings_equal("foo", "bar"):
        print("foo == bar: yes")
    else:
        print("foo == bar: no")

def test_comparison_with_literals() -> None:
    """Test comparing variables to string literals."""
    name: str = "Alice"

    if name == "Alice":
        print("name is Alice")

    if name != "Bob":
        print("name is not Bob")

    if name < "Bob":
        print("Alice < Bob: yes")

def find_string(items: list[str], target: str) -> int32:
    """Find index of string in list, -1 if not found."""
    i: int32 = 0
    while i < len(items):
        if items[i] == target:
            return i
        i += 1
    return -1

def test_comparison_in_loop() -> None:
    """Test string comparison in a loop."""
    names: list[str] = ["Alice", "Bob", "Charlie"]

    idx: int32 = find_string(names, "Bob")
    print(idx)

    idx = find_string(names, "Dave")
    print(idx)

# Run all tests
print("=== equality ===")
test_equality()
print("=== ordering ===")
test_ordering()
print("=== empty ===")
test_empty_strings()
print("=== function ===")
test_comparison_in_function()
print("=== literals ===")
test_comparison_with_literals()
print("=== loop ===")
test_comparison_in_loop()
