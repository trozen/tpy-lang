# User generic function: infer T from one argument and coerce another argument.
# This verifies Int32 -> Int64 coercion works during generic type argument inference.
from tpy import Int32, Int64

def echo_with_delta[T](x: T, delta: Int64) -> T:
    return x

value: Int32 = Int32(7)
print(echo_with_delta(value, Int32(5)))  # tpyc: ok
