# Cycle peer `a` defines a generic function whose parameter list
# carries `B` (a value-type record from peer `b`) by value. Generic
# functions emit their definition in the .hpp; the cycle's
# `_fwd.hpp` peer header does not provide B's complete layout at
# template instantiation. Reject at sema with a TPy diagnostic.
from a import first
from b import B

def main() -> None:
    print(first[int](7, B(99)))

main()
