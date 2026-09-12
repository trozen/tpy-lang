# Test error: generic function ref rejected when inferred type violates bound
from tpy import Fn, int32, Comparable

class Blob:
    x: int32

def max_val[T: Comparable](a: T, b: T) -> T:
    if a > b:
        return a
    return b

def apply2(f: Fn[[Blob, Blob], Blob], a: Blob, b: Blob) -> Blob:
    return f(a, b)

def main() -> None:
    a = Blob()
    b = Blob()
    apply2(max_val, a, b)  # tpyc: error(/does not satisfy bound/)

main()
