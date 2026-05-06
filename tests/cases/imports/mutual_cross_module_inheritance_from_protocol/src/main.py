# Cycle member explicitly inherits from a peer's protocol
# (`class Counter(Greeter)`). Allowed by design: protocols are
# structural / templated, not concrete inheritance, so the
# completeness-graph reject gate doesn't fire on cross-cycle
# protocol inheritance edges.
from a import Counter
from b import Greeter

def use_proto(g: Greeter) -> str:
    return g.hello()

def main() -> None:
    print(use_proto(Counter()))

main()
