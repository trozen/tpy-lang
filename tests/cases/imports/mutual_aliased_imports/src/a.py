from b import Helper as H
from tpy import Int32

class A:
    def __init__(self) -> None: pass
    def go(self) -> Int32:
        return H().work()
