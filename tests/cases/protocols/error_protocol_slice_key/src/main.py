# A protocol receiver whose __getitem__ declares a slice key cannot be
# sliced yet (BUGS.md#protocol-slice-key-rejected): the one remaining
# "Slicing is not supported" refusal.
from typing import Protocol
from tpy import int32


class Seq(Protocol):
    def __getitem__(self, part: slice) -> int32: ...


class Row:
    def __init__(self) -> None:
        pass

    def __getitem__(self, part: slice) -> int32:
        return 1


def first(s: Seq) -> None:
    print(s[0:1])  # tpyc: error(/Slicing is not supported for Seq/)


def main() -> None:
    first(Row())


main()
