from a import A
from tpy import int32

class B:
    n: int32
    payload: A
    def __init__(self, n: int32) -> None:
        self.n = n
