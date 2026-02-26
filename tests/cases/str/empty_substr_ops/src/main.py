# Test that count("") and replace("", ...) follow Python semantics
# instead of panicking on empty substring.

def test_count_empty() -> None:
    print("count hello:", "hello".count(""))
    print("count empty:", "".count(""))
    print("count single:", "x".count(""))

def test_replace_empty() -> None:
    print("replace hello:", "hello".replace("", "-"))
    print("replace empty:", "".replace("", "-"))
    print("replace single:", "x".replace("", "[]"))

def test_count_nonempty() -> None:
    print("count normal:", "banana".count("an"))
    print("count miss:", "hello".count("xyz"))

def test_replace_nonempty() -> None:
    print("replace normal:", "aabaa".replace("a", "x"))
    print("replace miss:", "hello".replace("xyz", "!"))

def main() -> None:
    test_count_empty()
    test_replace_empty()
    test_count_nonempty()
    test_replace_nonempty()

main()
