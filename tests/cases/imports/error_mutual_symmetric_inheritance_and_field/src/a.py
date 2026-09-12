from b import B  # tpyc: error(/concretely inherits/)
from tpy import int32

class A(B):
    extra: int32
    def __init__(self, n: int32, e: int32) -> None:
        B.__init__(self, n)
        self.extra = e
