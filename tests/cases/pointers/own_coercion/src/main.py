"""Tests that coercions work correctly through Own[T] wrapper.

Own[T] should transparently allow inner type coercions like BigInt -> Int32.
"""
from tpy import Int32, Own, copy

def return_owned_int32() -> Own[Int32]:
    big: int = 42
    return copy(big)  # BigInt -> Own[Int32] requires .to_int32() coercion

def take_owned_int32(x: Own[Int32]) -> Int32:
    return x

def main() -> None:
    # Test return coercion
    result1: Int32 = return_owned_int32()
    print(result1)  # 42

    # Test argument coercion
    big: int = 100
    result2: Int32 = take_owned_int32(big)
    print(result2)  # 100

main()
