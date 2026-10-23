# Slicing a protocol receiver whose __getitem__ takes a non-slice key is the
# key's type mismatch, named by the protocol's own parameter name.
from typing import Protocol
from tpy import int32


class Seq(Protocol):
    def __getitem__(self, pos: int32) -> int32: ...


class Row:
    def __init__(self) -> None:
        pass

    def __getitem__(self, pos: int32) -> int32:
        return pos


def first(s: Seq) -> None:
    print(s[0:1])  # tpyc: error(/Type mismatch in argument 'pos': expected int32, got slice/)


def main() -> None:
    first(Row())


main()
