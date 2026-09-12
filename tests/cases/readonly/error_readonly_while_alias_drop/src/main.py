# readonly[T]: alias from readonly param reassigned inside while loop --
# after loop, still readonly (loop may not execute).
from tpy import int32, readonly

class Box:
    v: int32
    def __init__(self, v: int32) -> None:
        self.v = v
    def set_v(self, v: int32) -> None:
        self.v = v

def observe(p: readonly[Box], flag: bool, q: Box) -> None:
    alias: Box = p
    while flag:
        alias = q
    alias.set_v(int32(99))  # tpyc: error(/readonly/)
