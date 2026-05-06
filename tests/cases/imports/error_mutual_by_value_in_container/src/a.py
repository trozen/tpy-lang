from b import B
from tpy import Int32

class A:
    children: list[B]  # tpyc: error(/Cyclic import/)
    def __init__(self) -> None:
        self.children = []
