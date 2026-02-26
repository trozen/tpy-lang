# F-string interpolation with various types and format specs.
from tpy import Int32


def test_basic() -> None:
    name: str = "world"
    print(f"hello {name}")
    print(f"")
    print(f"just text")


def test_types() -> None:
    x: int = 42
    pi: float = 3.14159
    flag: bool = True
    ch: str = "A"
    print(f"int={x}")
    print(f"float={pi}")
    print(f"bool={flag}")
    print(f"str={ch}")


def test_expressions() -> None:
    a: int = 10
    b: int = 20
    print(f"{a} + {b} = {a + b}")
    print(f"len({'hello'}) = {len('hello')}")


def test_format_spec() -> None:
    val: float = 3.14159
    print(f"{val:.2f}")
    n: Int32 = Int32(255)
    print(f"{n:#x}")
    print(f"{n:>10}")
    # '=' and '_' as fill characters (not alignment/grouping)
    print(f"{n:=<10}")
    print(f"{n:_>10}")


def test_braces() -> None:
    x: int = 42
    print(f"{{{x}}}")
    # Pure-literal braces (no interpolation)
    print(f"{{literal}}")


def test_multiple() -> None:
    a: str = "hello"
    b: int = 42
    c: bool = False
    print(f"{a} {b} {c}")


def test_adjacent() -> None:
    a: int = 1
    b: int = 2
    print(f"{a}{b}")


def test_fstring_var() -> None:
    x: int = 99
    s: str = f"value={x}"
    print(s)
    print(f"s={s}")


def test_str_conversion() -> None:
    x: int = 42
    print(f"{x!s}")


def test_bool_format() -> None:
    flag: bool = True
    print(f"{flag}")
    print(f"{flag:>10}")
    print(f"{flag:d}")
    off: bool = False
    print(f"{off}")
    print(f"{off:>10}")


test_basic()
test_types()
test_expressions()
test_format_spec()
test_braces()
test_multiple()
test_adjacent()
test_fstring_var()
test_str_conversion()
test_bool_format()
