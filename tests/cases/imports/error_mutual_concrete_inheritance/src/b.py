from a import A
from tpy import Int32

class B:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n

def take_a(a: A) -> Int32:
    return a.extra
