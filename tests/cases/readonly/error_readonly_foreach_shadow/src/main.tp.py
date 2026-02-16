# @readonly: loop var initialized from param, then shadowed by for-each --
# after loop, still readonly (conservative merge with pre-loop state).
from tpy import Int32, readonly

class Box:
    v: Int32
    def __init__(self, v: Int32) -> None:
        self.v = v

def mut(b: Box) -> None:
    b.v = Int32(1)

@readonly
def f(p: Box, xs: list[Box]) -> None:
    e = p
    for e in xs:
        pass
    mut(e)  # tpyc: error(/readonly/)
