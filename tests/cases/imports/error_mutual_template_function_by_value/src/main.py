# Cycle peer `a` defines an Fn-typed function `consume` whose
# signature carries `B` (a value-type record from peer `b`) by
# value. Because Fn-typed functions emit their definition in the
# .hpp, the cycle's `_fwd.hpp` peer header would not provide B's
# layout at template instantiation. The completeness-graph reject
# gate must catch this with a structured TPy diagnostic before the
# C++ build sees `return type 'struct B' is incomplete`.
from a import consume
from b import make_b

def main() -> None:
    print(consume(make_b).payload)

main()
