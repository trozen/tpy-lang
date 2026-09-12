from b import B
from tpy import int32, Fn

class A:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n

# Inline template -- Fn-typed param forces the body into a.hpp,
# and the body returns B by value, so a.hpp needs B's complete
# layout.
def make_b_via(maker: Fn[[], B]) -> B:  # tpyc: error(/Cyclic import/)
    return maker()
