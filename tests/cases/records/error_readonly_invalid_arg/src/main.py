# @readonly(1) with non-bool argument is rejected.
from tpy import int32, readonly

class Bad:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    @readonly(1)  # tpyc: error(/@readonly\(\) requires a bool argument/)
    def get_x(self) -> int32:
        return self.x

print(0)
