from tpy import Int32, Bool

# Test the 'not' logical operator

def test_not_with_bool_literals() -> None:
    """Test 'not' with Bool literals."""
    a: Bool = True
    b: Bool = False

    # Direct negation
    if not a:
        print("not True: yes")
    else:
        print("not True: no")

    if not b:
        print("not False: yes")
    else:
        print("not False: no")

def test_not_with_comparisons() -> None:
    """Test 'not' with comparison expressions."""
    x: Int32 = 5
    y: Int32 = 10

    # not with comparison
    if not (x > y):
        print("not (5 > 10): yes")

    if not (x == y):
        print("not (5 == 10): yes")

    if not (x < 0):
        print("not (5 < 0): yes")

def test_not_in_conditions() -> None:
    """Test 'not' combined with other logical operators."""
    a: Bool = True
    b: Bool = False

    # not with and
    if not a and b:
        print("not True and False: yes")
    else:
        print("not True and False: no")

    # not with or
    if not b or a:
        print("not False or True: yes")

    # Parenthesized not
    if not (a and b):
        print("not (True and False): yes")

    if not (a or b):
        print("not (True or False): yes")
    else:
        print("not (True or False): no")

def test_double_negation() -> None:
    """Test double negation."""
    flag: Bool = True

    if not not flag:
        print("not not True: yes")

    if not not False:
        print("not not False: yes")
    else:
        print("not not False: no")

def is_valid(x: Int32) -> Bool:
    """Helper function returning Bool."""
    return x > 0

def test_not_with_function_call() -> None:
    """Test 'not' with function return value."""
    if not is_valid(-5):
        print("not is_valid(-5): yes")

    if not is_valid(5):
        print("not is_valid(5): yes")
    else:
        print("not is_valid(5): no")

def test_not_in_while() -> None:
    """Test 'not' in while condition."""
    done: Bool = False
    count: Int32 = 0

    while not done:
        count += 1
        if count >= 3:
            done = True

    print(count)

# Run all tests
test_not_with_bool_literals()
test_not_with_comparisons()
test_not_in_conditions()
test_double_negation()
test_not_with_function_call()
test_not_in_while()
