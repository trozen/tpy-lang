# Test that iterating over string literals produces the same result as string variables,
# without including the null terminator.

def test_literal_iter() -> None:
    for ch in "abc":
        print(ch)

def test_var_iter() -> None:
    s = "abc"
    for ch in s:
        print(ch)

def test_empty_literal() -> None:
    count = 0
    for ch in "":
        count += 1
    print("empty:", count)

def test_single_char() -> None:
    for ch in "x":
        print(ch)

test_literal_iter()
test_var_iter()
test_empty_literal()
test_single_char()
