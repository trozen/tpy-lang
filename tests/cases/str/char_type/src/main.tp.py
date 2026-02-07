from tpy import Int32, Char, Bool

# Test Char type comprehensively

def test_char_literals() -> None:
    """Test single-character string literals as Char."""
    a: Char = "A"
    b: Char = "B"
    space: Char = " "
    newline: Char = "\n"

    print(a)
    print(b)
    print(space, end="")
    print("x")

def test_char_from_string_index() -> None:
    """Test getting Char from string indexing."""
    text: str = "Hello"

    c0: Char = text[0]
    c1: Char = text[1]
    c4: Char = text[4]

    print(c0)
    print(c1)
    print(c4)

def test_char_comparison() -> None:
    """Test Char comparison operators."""
    a: Char = "a"
    b: Char = "b"
    a2: Char = "a"

    # Equality
    if a == a2:
        print("a == a: yes")

    if a == b:
        print("a == b: yes")
    else:
        print("a == b: no")

    # Inequality
    if a != b:
        print("a != b: yes")

    # Ordering (lexicographic by ASCII)
    if a < b:
        print("a < b: yes")

    if b > a:
        print("b > a: yes")

    if a <= a2:
        print("a <= a: yes")

    if b >= a:
        print("b >= a: yes")

def test_char_in_string() -> None:
    """Test Char used with 'in' operator on string."""
    text: str = "hello world"
    target: Char = "o"
    missing: Char = "z"

    if target in text:
        print("o in text: yes")

    if missing in text:
        print("z in text: yes")
    else:
        print("z in text: no")

def is_vowel(c: Char) -> Bool:
    """Check if character is a vowel."""
    if c == "a":
        return True
    if c == "e":
        return True
    if c == "i":
        return True
    if c == "o":
        return True
    if c == "u":
        return True
    return False

def count_vowels(text: str) -> Int32:
    """Count vowels in a string."""
    count: Int32 = 0
    i: Int32 = 0
    while i < len(text):
        if is_vowel(text[i]):
            count += 1
        i += 1
    return count

def test_char_function_param() -> None:
    """Test Char as function parameter."""
    if is_vowel("a"):
        print("a is vowel")
    if is_vowel("b"):
        print("b is vowel")
    else:
        print("b is not vowel")
    if is_vowel("e"):
        print("e is vowel")
    if is_vowel("x"):
        print("x is vowel")
    else:
        print("x is not vowel")

def test_char_iteration() -> None:
    """Test Char from for-each iteration."""
    text: str = "abc"
    for c in text:
        print(c)

def test_vowel_counting() -> None:
    """Test counting vowels in string."""
    print(count_vowels("hello"))
    print(count_vowels("world"))
    print(count_vowels("aeiou"))
    print(count_vowels("xyz"))

def test_chr_function() -> None:
    """Test chr() returning Char."""
    c65: Char = chr(65)
    c97: Char = chr(97)

    print(c65)
    print(c97)

    # Compare chr result
    if c65 == "A":
        print("chr(65) == A: yes")

    if c97 == "a":
        print("chr(97) == a: yes")

# Run all tests
print("=== literals ===")
test_char_literals()
print("=== from index ===")
test_char_from_string_index()
print("=== comparison ===")
test_char_comparison()
print("=== in string ===")
test_char_in_string()
print("=== function param ===")
test_char_function_param()
print("=== iteration ===")
test_char_iteration()
print("=== vowel counting ===")
test_vowel_counting()
print("=== chr function ===")
test_chr_function()

def accepts_str(s: str) -> None:
    """Function that takes str parameter."""
    print(s)

def test_char_to_str_coercion() -> None:
    """Test single-char literal -> str coercion (works for literals only)."""
    # Assign single-char literal to str variable
    s1: str = "x"
    print(s1)

    # Pass single-char literal to str parameter
    accepts_str("w")

    # Multiple single-char str variables
    a: str = "a"
    b: str = "b"
    if a < b:
        print("a < b")

print("=== char to str ===")
test_char_to_str_coercion()
