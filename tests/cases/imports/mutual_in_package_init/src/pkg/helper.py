from pkg import use_pkg
from tpy import int32

class Boosted:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v
    def boost(self) -> int32:
        return self.val + use_pkg()
