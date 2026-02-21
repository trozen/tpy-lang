# @readonly: alias initialized from param, then one branch reassigns to param
# again -- after merge, alias is still readonly.
from tpy import Int32, readonly

class Box:
    v: Int32
    def __init__(self, v: Int32) -> None:
        self.v = v

def mutate(b: Box) -> None:
    b.v = Int32(99)

@readonly
def observe(flag: bool, p: Box) -> None:
    x: Box = p
    if flag:
        x = p
    else:
        x = Box(Int32(1))
    mutate(x)  # tpyc: error(/readonly/)
