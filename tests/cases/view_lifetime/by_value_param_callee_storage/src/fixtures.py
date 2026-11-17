# Shared types: a value record owning a string, and a class.
from tpy import ValueType, StrView, int32


class V(ValueType):
    def __init__(self, name: str) -> None:
        self.name = name

    def get(self) -> StrView:
        return self.name


class Sq:
    def __init__(self, s: int32) -> None:
        self.s = s


class P:
    def __init__(self, n: int32) -> None:
        self.n = n
