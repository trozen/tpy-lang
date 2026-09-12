# Error: constructor arity mismatch with default params
from tpy import int32

class Box:
    value: int32
    tag: str
    def __init__(self, value: int32, tag: str = "default") -> None:
        self.value = value
        self.tag = tag

Box()  # tpyc: error(/expects 1 to 2 arguments, got 0/)
