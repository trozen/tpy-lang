# @readonly(1) with non-bool argument is rejected.
from tpy import Int32, readonly

class Bad:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

    @readonly(1)  # tpyc: error(/@readonly\(\) requires a single bool argument/)
    def get_x(self) -> Int32:
        return self.x

print(0)
