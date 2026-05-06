from pkg import use_pkg
from tpy import Int32

class Boosted:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v
    def boost(self) -> Int32:
        return self.val + use_pkg()
