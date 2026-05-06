# Cycle peer `a` defines a function with a static-protocol param,
# which forces template emission in the .hpp. A by-value cycle-peer
# reference alongside the protocol param must be rejected by the
# completeness gate.
from a import measure
from b import B

def main() -> None:
    print(measure([1, 2, 3], B(10)))

main()
