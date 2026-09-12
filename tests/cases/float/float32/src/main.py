# float32 (32-bit single precision) type and float64 alias for float
from tpy import float32, float64, int32, int64, uint32

def test_construction() -> None:
    a = float32(3.14)  # tpyc: type(float32)
    b = float32(0.0)  # tpyc: type(float32)
    c = float32(int32(42))  # tpyc: type(float32)
    d = float32(True)  # tpyc: type(float32)
    e = float32("2.5")  # tpyc: type(float32)
    print(a)
    print(b)
    print(c)
    print(d)
    print(e)

def test_literal_coercion() -> None:
    # float literal -> float32 via coercion
    x: float32 = 1.5
    print(x)

    # int literal -> float32 via coercion
    y: float32 = 10
    print(y)

def test_arithmetic() -> None:
    a: float32 = float32(3.0)
    b: float32 = float32(2.0)

    # float32 + float32 -> float32
    print(a + b)
    print(a - b)
    print(a * b)
    print(a / b)
    print(a // b)
    print(a % b)
    print(a ** b)

def test_negation() -> None:
    x: float32 = float32(5.0)
    print(-x)

def test_mixed_with_float() -> None:
    # float32 + float -> float (widening)
    a: float32 = float32(1.5)
    b: float = a + 1.0
    print(b)

def test_mixed_with_int() -> None:
    # float32 + int32 -> float32
    a: float32 = float32(2.5)
    b: float32 = a + int32(1)
    print(b)

def test_augmented_assignment() -> None:
    x: float32 = float32(1.0)
    x += float32(0.5)
    print(x)
    x *= float32(2.0)
    print(x)

def test_conversions() -> None:
    a: float32 = float32(3.14)
    # float32 -> float
    b: float = float(a)
    print(b)
    # float32 -> str
    print(str(a))
    # float32 -> bool
    print(bool(a))
    print(bool(float32(0.0)))

def test_comparison() -> None:
    print(float32(1.0) < float32(2.0))
    print(float32(2.0) == float32(2.0))
    print(float32(3.0) > float32(1.0))

def test_fstring() -> None:
    v: float32 = float32(2.5)
    print(f"value={v}")

def test_float64_alias() -> None:
    # float64 is a type alias for float
    x: float64 = 3.14
    y: float64 = float64(2.0)
    print(x)
    print(y)
    print(x + y)
    # Constructor from fixed-width int types
    print(float64(int32(42)))
    print(float64(int64(100)))
    print(float64(uint32(7)))

def main() -> None:
    test_construction()
    test_literal_coercion()
    test_arithmetic()
    test_negation()
    test_mixed_with_float()
    test_mixed_with_int()
    test_augmented_assignment()
    test_conversions()
    test_comparison()
    test_fstring()
    test_float64_alias()

main()
