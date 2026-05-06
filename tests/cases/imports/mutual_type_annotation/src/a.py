from b import H
from tpy import Int32

class A:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v

    def go(self) -> Int32:
        return self.val

    # Calls into the cycle peer. b.H takes A by reference (TPy
    # auto-wraps the non-value record type), so the .hpp can hold
    # B's fwd-decl while the .cpp body sees full A. This is the
    # mutual case: a uses H by call, b uses A as a parameter type.
    def twice(self) -> Int32:
        return H(self) * 2
