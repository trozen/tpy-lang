# @readonly: passing a readonly param as mutable arg to a method is rejected.
from tpy import Int32, readonly

class Box:
    v: Int32
    def __init__(self, v: Int32) -> None:
        self.v = v

class Sink:
    def take(self, b: Box) -> None:
        b.v = Int32(123)

@readonly
def observe(p: Box) -> None:
    s = Sink()
    s.take(p)  # tpyc: error(/readonly/)
