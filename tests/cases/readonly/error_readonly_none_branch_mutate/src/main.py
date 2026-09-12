# None-seeded variable assigned from a readonly param inside a branch
# should remain readonly after the branch -- mutation must be rejected.
from tpy import int32, readonly

class Box:
    v: int32
    def __init__(self, v: int32) -> None:
        self.v = v

    def set_v(self, x: int32) -> None:
        self.v = x

@readonly
def observe(flag: bool, p: Box) -> None:
    x = None
    if flag:
        x = p
    if x is not None:
        x.set_v(int32(1))  # tpyc: error(/readonly/)
