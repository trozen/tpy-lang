# Error: constructor called with too many args (has defaults)
from tpy import Int32

class Box:
    value: Int32
    tag: str
    def __init__(self, value: Int32, tag: str = "default") -> None:
        self.value = value
        self.tag = tag

Box(Int32(1), "a", "b")  # tpyc: error(/expects 1 to 2 arguments, got 3/)
