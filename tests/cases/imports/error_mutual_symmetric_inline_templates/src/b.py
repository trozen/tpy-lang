from a import A
from tpy import int32, Fn

class B:
    m: int32
    def __init__(self, m: int32) -> None:
        self.m = m

# Symmetric -- inline template returning A by value. b.hpp needs
# A's complete layout for the same reason a.hpp needs B's.
def make_a_via(maker: Fn[[], A]) -> A:
    return maker()
