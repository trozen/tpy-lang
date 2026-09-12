from a import AType
from tpy import int32

class BType:
    tag: int32
    def __init__(self) -> None:
        self.tag = int32(7)
    def use_a(self, a: AType) -> int32:
        return self.tag
