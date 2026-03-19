# Test error: generic function ref where type param cannot be inferred from hint
from tpy import Fn, Int32

def factory[T](n: Int32) -> Int32:
    return n

# T does not appear in params or return, so it cannot be inferred from the hint
def apply(f: Fn[[Int32], Int32], x: Int32) -> Int32:
    return f(x)

def main() -> None:
    apply(factory, 42)  # tpyc: error(/cannot infer type parameter/)

main()
