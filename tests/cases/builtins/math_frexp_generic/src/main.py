# math.frexp is generic over the exponent type. Covers three ways to pick T:
#   (a) default T -- DefaultInt (int32 under default config)
#   (b) explicit subscript `frexp[T](x)` -- TPy-only syntax
#   (c) bi-directional inference from annotated target / return-type context
# TPy-only because (b) uses subscript syntax CPython's math.frexp doesn't
# support; (c) forms compile under CPython but wouldn't test inference there.
import math
from tpy import int64

def wrap_bigint() -> tuple[float, int]:
    # Return-type context flows back: T inferred as BigInt.
    return math.frexp(2.0)

def main() -> None:
    # (a) Default T -- DefaultInt (int32 by default)
    m1, e1 = math.frexp(12.0)
    print(m1, e1)

    # (b) Explicit subscript: int64 and BigInt (Python int)
    m2, e2 = math.frexp[int64](12.0)
    print(m2, e2)
    m3, e3 = math.frexp[int](12.0)
    print(m3, e3)

    # (c) Inference via annotated tuple target -- T becomes BigInt
    result: tuple[float, int] = math.frexp(1.5)
    print(result[0], result[1])

    # (c) Inference via return-type context in a nested call
    a, b = wrap_bigint()
    print(a, b)

    # Round-trip through ldexp using the int32 default
    mm, ee = math.frexp(7.25)
    print(math.ldexp(mm, ee))

main()
