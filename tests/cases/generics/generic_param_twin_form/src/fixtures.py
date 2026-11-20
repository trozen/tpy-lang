# Shared types: a box whose field a body mutates while the caller passed that
# field, a ValueType holding a str, the protocols it conforms to, a class.
from typing import Protocol
from tpy import StrView, ValueType, int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


class Text:
    def __init__(self, s: str) -> None:
        self.s = s


class V(ValueType):
    s: str

    def __init__(self, s: str) -> None:
        self.s = s

    def label(self) -> str:
        # the copy is the protocol's `-> str`, not this case's subject
        return self.s  # tpyc: warning(/returns a copy of str field/)

    def get(self) -> StrView:
        return self.s


class Labeled(Protocol):
    def label(self) -> str: ...


class Named(Protocol):
    def get(self) -> StrView: ...


class C:
    def __init__(self, n: int32) -> None:
        self.n = n
