# Float32 (32-bit single precision) type and Float64 alias for float
from tpy import Float32, Float64, Int32

def test_construction() -> None:
    a = Float32(3.14)  # tpyc: type(Float32)
    b = Float32(0.0)  # tpyc: type(Float32)
    c = Float32(Int32(42))  # tpyc: type(Float32)
    d = Float32(True)  # tpyc: type(Float32)
    e = Float32("2.5")  # tpyc: type(Float32)
    print(a)
    print(b)
    print(c)
    print(d)
    print(e)

def test_literal_coercion() -> None:
    # float literal -> Float32 via coercion
    x: Float32 = 1.5
    print(x)

    # int literal -> Float32 via coercion
    y: Float32 = 10
    print(y)

def test_arithmetic() -> None:
    a: Float32 = Float32(3.0)
    b: Float32 = Float32(2.0)

    # Float32 + Float32 -> Float32
    print(a + b)
    print(a - b)
    print(a * b)
    print(a / b)
    print(a // b)
    print(a % b)
    print(a ** b)

def test_negation() -> None:
    x: Float32 = Float32(5.0)
    print(-x)

def test_mixed_with_float() -> None:
    # Float32 + float -> float (widening)
    a: Float32 = Float32(1.5)
    b: float = a + 1.0
    print(b)

def test_mixed_with_int() -> None:
    # Float32 + Int32 -> Float32
    a: Float32 = Float32(2.5)
    b: Float32 = a + Int32(1)
    print(b)

def test_augmented_assignment() -> None:
    x: Float32 = Float32(1.0)
    x += Float32(0.5)
    print(x)
    x *= Float32(2.0)
    print(x)

def test_conversions() -> None:
    a: Float32 = Float32(3.14)
    # Float32 -> float
    b: float = float(a)
    print(b)
    # Float32 -> str
    print(str(a))
    # Float32 -> bool
    print(bool(a))
    print(bool(Float32(0.0)))

def test_comparison() -> None:
    print(Float32(1.0) < Float32(2.0))
    print(Float32(2.0) == Float32(2.0))
    print(Float32(3.0) > Float32(1.0))

def test_fstring() -> None:
    v: Float32 = Float32(2.5)
    print(f"value={v}")

def test_float64_alias() -> None:
    # Float64 is an alias for float
    x: Float64 = 3.14
    y: Float64 = Float64(2.0)
    print(x)
    print(y)
    print(x + y)

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
