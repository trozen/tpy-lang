# Import-cycle member (with z_peer and n_native) whose defaults name a later
# peer's enum member and a constant of a module outside the cycle.
from tpy import int32
import z_peer
import n_native
from z_peer import E
from const import K


class Q:
    def __init__(self) -> None:
        pass

    # Before a required keyword-only parameter, so every call fills it.
    def add(self, e: E = E.B, *, c: int32) -> int32:
        return e.value * 10 + c

    # A trailing default naming the peer's enum member: the header sees the
    # peer only through its forward declarations, so every call fills it.
    def add_trailing(self, e: E = E.B) -> int32:
        return e.value


# a call from the declaring member of the cycle
def own_call() -> int32:
    return Q().add_trailing()


def f(x: int32 = K, y: int32 = 0) -> int32:
    return x * 10 + y
