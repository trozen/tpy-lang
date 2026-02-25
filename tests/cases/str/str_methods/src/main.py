# Test str methods: strip, replace, find, startswith, upper, lower, count, is*
def test_strip() -> None:
    print("strip:", "  hello  ".strip())
    print("lstrip:", "  hello  ".lstrip())
    print("rstrip:", "  hello  ".rstrip())
    print("strip tabs:", "\t hi \n".strip())

def test_replace() -> None:
    print("replace:", "aabaa".replace("a", "x"))
    print("replace once:", "hello world".replace("o", "0"))

def test_find() -> None:
    print("find:", "hello".find("ll"))
    print("find miss:", "hello".find("xyz"))
    print("rfind:", "abcabc".rfind("abc"))
    print("rfind miss:", "hello".rfind("xyz"))

def test_index() -> None:
    print("index:", "hello".index("ell"))

def test_startswith_endswith() -> None:
    s = "hello world"
    print("startswith:", s.startswith("hello"))
    print("startswith miss:", s.startswith("world"))
    print("endswith:", s.endswith("world"))
    print("endswith miss:", s.endswith("hello"))
    print("empty prefix:", s.startswith(""))
    print("empty suffix:", s.endswith(""))

def test_upper_lower() -> None:
    print("upper:", "Hello World 123".upper())
    print("lower:", "Hello World 123".lower())
    print("upper empty:", "".upper())
    print("lower empty:", "".lower())

def test_count() -> None:
    print("count:", "banana".count("an"))
    print("count miss:", "hello".count("xyz"))
    print("count single:", "aaa".count("a"))

def test_is_methods() -> None:
    print("isdigit 123:", "123".isdigit())
    print("isdigit abc:", "abc".isdigit())
    print("isdigit empty:", "".isdigit())
    print("isalpha abc:", "abc".isalpha())
    print("isalpha 123:", "123".isalpha())
    print("isalpha empty:", "".isalpha())
    print("isalnum a1:", "abc123".isalnum())
    print("isalnum !:", "abc!".isalnum())
    print("isalnum empty:", "".isalnum())
    print("isspace:", "  \t\n".isspace())
    print("isspace no:", "  a  ".isspace())
    print("isspace empty:", "".isspace())

def test_isupper_islower() -> None:
    print("isupper ABC:", "ABC".isupper())
    print("isupper abc:", "abc".isupper())
    print("isupper ABC123:", "ABC123".isupper())
    print("isupper 123:", "123".isupper())
    print("isupper empty:", "".isupper())
    print("islower abc:", "abc".islower())
    print("islower ABC:", "ABC".islower())
    print("islower abc123:", "abc123".islower())
    print("islower 123:", "123".islower())
    print("islower empty:", "".islower())

def test_capitalize_title_swapcase() -> None:
    print("capitalize:", "hello world".capitalize())
    print("capitalize upper:", "HELLO".capitalize())
    print("capitalize empty:", "".capitalize())
    print("title:", "hello world foo".title())
    print("title mixed:", "they're bill's".title())
    print("swapcase:", "Hello World".swapcase())
    print("swapcase empty:", "".swapcase())

def test_removeprefix_removesuffix() -> None:
    print("removeprefix:", "TestCase".removeprefix("Test"))
    print("removeprefix miss:", "TestCase".removeprefix("Foo"))
    print("removesuffix:", "TestCase".removesuffix("Case"))
    print("removesuffix miss:", "TestCase".removesuffix("Foo"))
    print("removesuffix empty:", "hello".removesuffix(""))

def test_rindex() -> None:
    print("rindex:", "abcabc".rindex("abc"))

def test_splitlines() -> None:
    lines = "one\ntwo\nthree".splitlines()
    print("splitlines:", len(lines), lines[0], lines[1], lines[2])
    lines2 = "a\r\nb\nc".splitlines()
    print("splitlines crlf:", len(lines2), lines2[0], lines2[1], lines2[2])

def test_chaining() -> None:
    result = "  Hello, World!  ".strip().lower().replace("world", "python")
    print("chain:", result)

test_strip()
test_replace()
test_find()
test_index()
test_startswith_endswith()
test_upper_lower()
test_count()
test_is_methods()
test_isupper_islower()
test_capitalize_title_swapcase()
test_removeprefix_removesuffix()
test_rindex()
test_splitlines()
test_chaining()
