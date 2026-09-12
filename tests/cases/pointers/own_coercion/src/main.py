"""Tests that coercions work correctly through Own[T] wrapper.

Own[T] should transparently allow inner type coercions like BigInt -> int32.
"""
from tpy import int32, Own, copy

def return_owned_int32() -> Own[int32]:
    big: int = 42
    return copy(big)  # BigInt -> Own[int32] requires .to_int32() coercion

def take_owned_int32(x: Own[int32]) -> int32:
    return x

g: int = 55

def global_source() -> int32:
    return take_owned_int32(g)  # tpyc: ok -- a bare module-global read at the Own slot

def main() -> None:
    # Test return coercion
    result1: int32 = return_owned_int32()
    print(result1)  # 42

    # Test argument coercion
    big: int = 100
    result2: int32 = take_owned_int32(big)
    print(result2)  # 100

    print(global_source())  # 55

main()
