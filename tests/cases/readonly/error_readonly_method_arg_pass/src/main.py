# @readonly: passing a readonly param as mutable arg to a method is rejected.
from tpy import int32, readonly

class Box:
    v: int32
    def __init__(self, v: int32) -> None:
        self.v = v

class Sink:
    def take(self, b: Box) -> None:
        b.v = int32(123)

@readonly
def observe(p: Box) -> None:
    s = Sink()
    s.take(p)  # tpyc: error(/readonly/)
