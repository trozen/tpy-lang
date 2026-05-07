from a import AType
from tpy import Int32

class BType:
    tag: Int32
    def __init__(self) -> None:
        self.tag = Int32(7)
    def use_a(self, a: AType) -> Int32:
        return self.tag
