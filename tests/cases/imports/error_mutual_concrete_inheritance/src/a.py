from b import B
from tpy import int32

class A(B):  # tpyc: error(/concretely inherits/)
    extra: int32
    def __init__(self, n: int32, e: int32) -> None:
        B.__init__(self, n)
        self.extra = e
