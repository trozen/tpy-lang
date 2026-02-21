# None-seeded variable assigned from a readonly param inside a branch
# should remain readonly after the branch -- mutation must be rejected.
from tpy import Int32, readonly

class Box:
    v: Int32
    def __init__(self, v: Int32) -> None:
        self.v = v

    def set_v(self, x: Int32) -> None:
        self.v = x

@readonly
def observe(flag: bool, p: Box) -> None:
    x = None
    if flag:
        x = p
    if x is not None:
        x.set_v(Int32(1))  # tpyc: error(/readonly/)
