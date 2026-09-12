# User generic function: infer T from one argument and coerce another argument.
# This verifies int32 -> int64 coercion works during generic type argument inference.
from tpy import int32, int64

def echo_with_delta[T](x: T, delta: int64) -> T:
    return x

value: int32 = int32(7)
print(echo_with_delta(value, int32(5)))  # tpyc: ok
