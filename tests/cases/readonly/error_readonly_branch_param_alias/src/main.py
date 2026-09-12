# @readonly: alias initialized from param, then one branch reassigns to param
# again -- after merge, alias is still readonly.
from tpy import int32, readonly

class Box:
    v: int32
    def __init__(self, v: int32) -> None:
        self.v = v

def mutate(b: Box) -> None:
    b.v = int32(99)

@readonly
def observe(flag: bool, p: Box) -> None:
    x: Box = p
    if flag:
        x = p
    else:
        x = Box(int32(1))
    mutate(x)  # tpyc: error(/readonly/)
