# Error: constructor called with too many args (has defaults)
from tpy import int32

class Box:
    value: int32
    tag: str
    def __init__(self, value: int32, tag: str = "default") -> None:
        self.value = value
        self.tag = tag

Box(int32(1), "a", "b")  # tpyc: error(/takes 1 to 2 positional arguments, got 3/)
