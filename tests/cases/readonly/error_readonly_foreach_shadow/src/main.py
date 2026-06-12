# @readonly: an alias of the readonly param cannot be rebound by a
# for-each (reference-type rebind is rejected outright).
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
    for e in xs:  # tpyc: error(/for-loop rebind of reference-type variable 'e'/)
        pass
    mut(e)
