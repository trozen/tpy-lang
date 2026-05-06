from a import A
from tpy import Int32

class B:
    n: Int32
    payload: A
    def __init__(self, n: Int32) -> None:
        self.n = n
