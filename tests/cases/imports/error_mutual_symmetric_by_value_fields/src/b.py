from a import A
from tpy import Int32

class B:
    m: Int32
    other: A
    def __init__(self, m: Int32, o: A) -> None:
        self.m = m
        self.other = o
