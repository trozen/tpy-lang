# Test error: generic function ref where type param cannot be inferred from hint
from tpy import Fn, int32

def factory[T](n: int32) -> int32:
    return n

# T does not appear in params or return, so it cannot be inferred from the hint
def apply(f: Fn[[int32], int32], x: int32) -> int32:
    return f(x)

def main() -> None:
    apply(factory, 42)  # tpyc: error(/cannot infer type parameter/)

main()
