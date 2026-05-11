# Nested tuple satisfies Comparable when all (recursively-)elements do.
# Guards the recursion in the tuple<->Comparable intrinsic against future
# rewrites that might forget to delegate through classify_protocol_conformance.
from tpy import Comparable

def less[T: Comparable](a: T, b: T) -> bool:
    return a < b

def main() -> None:
    a = ((1, 2), "x")
    b = ((1, 3), "x")
    c = ((1, 2), "y")

    print(less(a, b))   # inner-tuple decides: (1,2) < (1,3)
    print(less(a, c))   # outer string decides: "x" < "y"
    print(less(b, a))   # False

main()
