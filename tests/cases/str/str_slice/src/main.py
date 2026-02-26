# String slicing with Python semantics (clamping, negative indices).


def test_basic() -> None:
    s: str = "hello world"
    print(s[0:5])
    print(s[6:11])
    print(s[6:])
    print(s[:5])
    print(s[:])


def test_negative() -> None:
    s: str = "abcdef"
    print(s[-3:])
    print(s[:-2])
    print(s[-4:-1])
    print(s[-6:])


def test_clamping() -> None:
    s: str = "hello"
    print(s[0:100])
    print(s[-100:3])
    print(s[-100:100])
    print(s[10:20])


def test_empty() -> None:
    s: str = "hello"
    print(len(s[3:1]))
    print(len(s[5:5]))
    print(len(s[2:2]))


def test_param(s: str) -> None:
    r = s[1:4]  # tpyc: type(StrView)
    print(r)


def test_local_type() -> None:
    s: str = "abcdef"
    r = s[1:3]  # tpyc: type(StrView)
    print(r)


def test_single_char() -> None:
    s: str = "hello"
    print(s[0:1])
    print(s[-1:])


test_basic()
print("---")
test_negative()
print("---")
test_clamping()
print("---")
test_empty()
print("---")
test_param("abcdef")
print("---")
test_local_type()
print("---")
test_single_char()
