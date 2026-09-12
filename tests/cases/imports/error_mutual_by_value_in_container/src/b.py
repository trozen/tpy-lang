from a import A
from tpy import int32

class B:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n

def take_a(a: A) -> int32:
    return len(a.children)
