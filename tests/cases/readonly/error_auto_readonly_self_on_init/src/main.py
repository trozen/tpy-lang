# auto_readonly[Self] is not allowed on __init__.
from typing import Self
from tpy import auto_readonly

class Foo:
    x: int
    def __init__(self: auto_readonly[Self]) -> None:  # tpyc: error(/auto_readonly\[Self\] is not allowed on '__init__'/)
        self.x = 1
