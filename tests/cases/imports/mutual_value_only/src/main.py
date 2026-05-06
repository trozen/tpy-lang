# Pure value-level mutual reference. a.py defines K(), imports H from b
# and uses it; b.py defines H(), imports K from a and uses it. Both
# modules call across the cycle. Sema accepts via skeletal FunctionInfo
# pre-registration; the C++ build uses fwd.hpp to break the
# complete-type cyclic include.
from a import K, K_then_H
from b import H

def main() -> None:
    print(K())
    print(H())
    print(K_then_H())

main()
