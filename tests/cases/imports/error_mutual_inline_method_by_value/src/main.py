# Cycle peer `a` defines a class `A` with a generic method `merge`
# whose definition is emitted inline in `a.hpp`. The by-value
# parameter `b: B` references a value-type record from peer `b`,
# whose layout the cycle's `_fwd.hpp` does not provide. The gate
# must reject the method's signature.
from a import A
from b import B

def main() -> None:
    a = A(7)
    print(a.merge[int](B(99), 5))

main()
