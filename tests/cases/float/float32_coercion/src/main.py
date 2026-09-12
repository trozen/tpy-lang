# float32 coercion: widening, narrowing, function params, return types, type inference
from tpy import float32, int32, int64, uint8

def accepts_float(x: float) -> float:
    return x + 1.0

def accepts_f32(x: float32) -> float32:
    return x + float32(1.0)

def returns_f32() -> float32:
    return float32(3.0)

def test_widening_assignment() -> None:
    # float32 -> float (implicit widening)
    a: float32 = float32(2.5)
    b: float = a  # tpyc: ok
    print(b)

def test_narrowing_assignment() -> None:
    # float -> float32 (implicit narrowing, allowed for convenience)
    a: float = 2.5
    b: float32 = a  # tpyc: ok
    print(b)

def test_param_coercion() -> None:
    # Pass float32 where float expected (widening)
    v: float32 = float32(4.0)
    r = accepts_float(v)  # tpyc: type(float)
    print(r)

    # Pass float where float32 expected (narrowing)
    w: float = 4.0
    r2 = accepts_f32(w)  # tpyc: type(float32)
    print(r2)

def test_return_coercion() -> None:
    # Return float32 into float variable
    f: float = returns_f32()  # tpyc: ok
    print(f)

def test_mixed_type_inference() -> None:
    a: float32 = float32(1.0)
    b: float32 = float32(2.0)
    c = a + b  # tpyc: type(float32)
    print(c)

    d: float = 3.0
    e = a + d  # tpyc: type(float)
    print(e)

    f = a + int32(5)  # tpyc: type(float32)
    print(f)

def test_int_to_float32_coercion() -> None:
    # Various int types -> float32 via coercion
    a: float32 = int32(10)  # tpyc: ok
    print(a)

    b: float32 = int64(20)  # tpyc: ok
    print(b)

    c: float32 = uint8(30)  # tpyc: ok
    print(c)

def test_chained_coercion() -> None:
    # float32 -> float -> used in float arithmetic
    a: float32 = float32(1.5)
    b: float = a * 2.0  # float32 * float_literal -> float32 (literal adapts), widened to float
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
