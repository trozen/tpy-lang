# Float32 coercion: widening, narrowing, function params, return types, type inference
from tpy import Float32, Int32, Int64, UInt8

def accepts_float(x: float) -> float:
    return x + 1.0

def accepts_f32(x: Float32) -> Float32:
    return x + Float32(1.0)

def returns_f32() -> Float32:
    return Float32(3.0)

def test_widening_assignment() -> None:
    # Float32 -> float (implicit widening)
    a: Float32 = Float32(2.5)
    b: float = a  # tpyc: ok
    print(b)

def test_narrowing_assignment() -> None:
    # float -> Float32 (implicit narrowing, allowed for convenience)
    a: float = 2.5
    b: Float32 = a  # tpyc: ok
    print(b)

def test_param_coercion() -> None:
    # Pass Float32 where float expected (widening)
    v: Float32 = Float32(4.0)
    r = accepts_float(v)  # tpyc: type(float)
    print(r)

    # Pass float where Float32 expected (narrowing)
    w: float = 4.0
    r2 = accepts_f32(w)  # tpyc: type(Float32)
    print(r2)

def test_return_coercion() -> None:
    # Return Float32 into float variable
    f: float = returns_f32()  # tpyc: ok
    print(f)

def test_mixed_type_inference() -> None:
    a: Float32 = Float32(1.0)
    b: Float32 = Float32(2.0)
    c = a + b  # tpyc: type(Float32)
    print(c)

    d: float = 3.0
    e = a + d  # tpyc: type(float)
    print(e)

    f = a + Int32(5)  # tpyc: type(Float32)
    print(f)

def test_int_to_float32_coercion() -> None:
    # Various int types -> Float32 via coercion
    a: Float32 = Int32(10)  # tpyc: ok
    print(a)

    b: Float32 = Int64(20)  # tpyc: ok
    print(b)

    c: Float32 = UInt8(30)  # tpyc: ok
    print(c)

def test_chained_coercion() -> None:
    # Float32 -> float -> used in float arithmetic
    a: Float32 = Float32(1.5)
    b: float = a * 2.0  # Float32 * float -> float
    c: float = b + 1.0
    print(c)

def main() -> None:
    test_widening_assignment()
    test_narrowing_assignment()
    test_param_coercion()
    test_return_coercion()
    test_mixed_type_inference()
    test_int_to_float32_coercion()
    test_chained_coercion()

main()
