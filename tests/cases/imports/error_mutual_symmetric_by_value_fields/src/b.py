from a import A
from tpy import int32

class B:
    m: int32
    other: A
    def __init__(self, m: int32, o: A) -> None:
        self.m = m
        self.other = o
