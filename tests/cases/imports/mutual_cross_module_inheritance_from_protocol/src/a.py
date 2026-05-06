from b import Greeter

# Cross-module inheritance from a peer's *protocol*. Protocols are
# structural / templated (no concrete C++ layout), so the
# completeness-graph reject gate accepts the inheritance edge across
# the cycle -- the gate rejects only inheritance from peer concrete
# records.
class Counter(Greeter):
    def __init__(self) -> None: pass

    def hello(self) -> str:
        return "hi from a.Counter"
