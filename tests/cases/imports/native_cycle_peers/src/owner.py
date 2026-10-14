# Section self_reexport: a module re-imports its own names through a native peer.
__tpy__ = {"includes": ["cycle_types.hpp"]}
from tpy import int32

class Slot:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

def seven() -> int32:
    return 7

# Both names are defined above; the aliases must not precede them in C++.
from relay import Slot as RelaySlot, seven as relay_seven  # tpyc: warning(/Import statement should be at the top/)

def bump(s: RelaySlot) -> None:
    s.v += relay_seven()
