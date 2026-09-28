# Test and/or returning operand values (Python semantics), not bool.
from typing import Iterator
from tpy import int32

def test_or_int() -> None:
    a: int32 = 0
    b: int32 = 42
    x = a or b  # tpyc: type(int32)
    print(x)

    c: int32 = 1
    d: int32 = 2
    y = c or d  # tpyc: type(int32)
    print(y)

def test_and_int() -> None:
    a: int32 = 0
    b: int32 = 42
    x = a and b  # tpyc: type(int32)
    print(x)

    c: int32 = 1
    d: int32 = 2
    y = c and d  # tpyc: type(int32)
    print(y)

def test_or_str() -> None:
    empty: str = ""
    fallback: str = "default"
    x = empty or fallback  # tpyc: type(StrView)
    print(x)

    name: str = "alice"
    y = name or fallback  # tpyc: type(StrView)
    print(y)

def test_and_str() -> None:
    empty: str = ""
    fallback: str = "world"
    x = empty and fallback  # tpyc: type(StrView)
    print(x)

    name: str = "hello"
    y = name and fallback  # tpyc: type(StrView)
    print(y)

def test_or_float() -> None:
    a: float = 0.0
    b: float = 3.14
    x = a or b  # tpyc: type(float)
    print(x)

    c: float = 1.5
    d: float = 2.5
    y = c or d  # tpyc: type(float)
    print(y)

def test_and_float() -> None:
    a: float = 0.0
    b: float = 3.14
    x = a and b  # tpyc: type(float)
    print(x)

    c: float = 1.5
    d: float = 2.5
    y = c and d  # tpyc: type(float)
    print(y)

def test_or_bigint() -> None:
    a: int = 0
    b: int = 100
    x = a or b  # tpyc: type(int)
    print(x)

def test_chained() -> None:
    a: int32 = 0
    b: int32 = 0
    c: int32 = 3
    x = a or b or c  # tpyc: type(int32)
    print(x)

    d: int32 = 1
    e: int32 = 2
    f: int32 = 3
    y = d and e and f  # tpyc: type(int32)
    print(y)

def test_or_with_literal() -> None:
    a: int32 = 0
    x = a or 99  # tpyc: type(int32)
    print(x)

def accepts_int(v: int32) -> None:
    print(v)

def returns_int(a: int32, b: int32) -> int32:
    return a or b

def test_as_arg_and_return() -> None:
    a: int32 = 0
    b: int32 = 7
    accepts_int(a or b)
    print(returns_int(0, 5))

def test_bool_operands(flag: bool, other: bool) -> bool:
    # Both operands are already bool, so the value-select result IS bool.
    return flag and other  # tpyc: ok

def test_mixed_returns_bool() -> None:
    """Mixed types fall back to bool (condition context unaffected)."""
    a: int32 = 1
    b: float = 2.0
    if a and b:  # tpyc: ok
        print("mixed condition ok")

def test_condition_context() -> None:
    a: int32 = 1
    b: int32 = 2
    if a and b:
        print("both truthy")
    if a or b:
        print("at least one truthy")

class Counter:
    count: int32
    def __init__(self, n: int32) -> None:
        self.count = n
    def __bool__(self) -> bool:
        return self.count != 0

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def test_record_with_bool() -> None:
    zero: Counter = Counter(0)
    five: Counter = Counter(5)
    x = zero or five
    print(x.count)
    y = five and zero
    print(y.count)
    # Mutation via result must affect the original (reference semantics)
    x.count = 99
    print(five.count)   # 99: x references five
    y.count = 77
    print(zero.count)   # 77: y references zero

def test_record_without_bool() -> None:
    # Records without __bool__ are always truthy (Python default)
    a: Point = Point(1, 2)
    b: Point = Point(3, 4)
    x = a or b
    print(x.x)
    y = a and b
    print(y.x)
    # Mutation via result must affect the original (reference semantics)
    x.x = 99
    print(a.x)   # 99: x references a (always truthy)
    y.x = 77
    print(b.x)   # 77: y references b (and returns rhs when lhs truthy)

def test_record_or_constructor() -> None:
    # Mixed lvalue/rvalue: must not create dangling reference
    zero: Counter = Counter(0)
    x = zero or Counter(5)
    print(x.count)
    five: Counter = Counter(5)
    y = five and Counter(0)
    print(y.count)

def test_annotated() -> None:
    a: int32 = 0
    b: int32 = 42
    x: int32 = a or b  # tpyc: type(int32)
    print(x)

    c: int32 = 1
    d: int32 = 2
    y: int32 = c and d  # tpyc: type(int32)
    print(y)

    empty: str = ""
    fallback: str = "default"
    s: str = empty or fallback  # tpyc: type(str)
    print(s)

    fa: float = 0.0
    fb: float = 3.14
    f: float = fa or fb  # tpyc: type(float)
    print(f)

def test_literal_or_literal() -> None:
    x = 0 or 1  # tpyc: type(int32)
    print(x)
    y = 3 and 0  # tpyc: type(int32)
    print(y)

    # Annotated as int (BigInt) -- annotation drives the type
    xi: int = 0 or 1  # tpyc: type(int)
    print(xi)
    yi: int = 3 and 0  # tpyc: type(int)
    print(yi)

# A literal left operand with a variable right one, at the free-function,
# method and generator positions.
def test_literal_lhs_free(a: float) -> None:
    # free function: int literal joined with a float operand
    w: float = 0 or a  # tpyc: type(float)
    print("free:", w)
    n = 5
    k = 0 and n  # tpyc: type(int32)
    print("free:", k)
    # free function: float and negative literals, and a small int literal
    # against an `int` operand, which converts only in its chosen arm
    f = 0.0 or a  # tpyc: type(float)
    m = -1 and n  # tpyc: type(int32)
    big: int = 7
    s = 0 or big  # tpyc: type(int)
    print("free:", f, m, s)
    # free function: a literal beyond int64 is an `int` construction
    h: int = 123456789012345678901234567890 or big
    print("free:", h)

class LiteralLhs:
    def __init__(self, v: int32) -> None:
        self.v = v

    def pick(self) -> int32:
        # method: literal LHS against a field
        return 0 or self.v  # tpyc: ok

def gen_literal_lhs(v: int32) -> Iterator[int32]:
    # generator: literal LHS in a yield and in a local
    yield 7 and v  # tpyc: ok
    w = 0 or v  # tpyc: type(int32)
    yield w

def main() -> None:
    test_or_int()
    test_and_int()
    test_or_str()
    test_and_str()
    test_or_float()
    test_and_float()
    test_or_bigint()
    test_chained()
    test_or_with_literal()
    test_as_arg_and_return()
    print(test_bool_operands(True, False), test_bool_operands(True, True))
    test_mixed_returns_bool()
    test_condition_context()
    test_record_with_bool()
    test_record_without_bool()
    test_record_or_constructor()
    test_annotated()
    test_literal_or_literal()
    test_literal_lhs_free(2.5)
    print("method:", LiteralLhs(4).pick())
    for g in gen_literal_lhs(3):
        print("generator:", g)

main()
