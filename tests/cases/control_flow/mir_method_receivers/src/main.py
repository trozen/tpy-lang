# Tuple and Optional captures of self retain shared identity; readonly self
# observes other writers. Explicit copy intentionally creates an independent cell.
from __future__ import annotations

from tpy import copy, int32


class Cell:
    value: int32

    def __init__(self, value: int32):
        self.value = value

    def singleton(self) -> int32:
        packed = (self,)
        # A singleton tuple holds the same receiver, not a copy.
        packed[0].value = 12  # tpyc: ok
        return self.value

    def mixed(self) -> int32:
        packed = (self, self.value)
        # The record remains shared while its captured scalar keeps the old value.
        packed[0].value = 13  # tpyc: ok
        return packed[1]

    def inferred(self, other: Cell) -> int32:
        saved = self
        # Readonly self must still observe mutation through a shared mutable argument.
        other.value = 14  # tpyc: ok
        return saved.value

    def optional(self, other: Cell) -> int32:
        saved: Cell | None = self
        # Wrapping self in Optional must preserve observation through another alias.
        other.value = 17  # tpyc: ok
        if saved is not None:
            return saved.value
        return 0

    def owned_copy(self) -> int32:
        saved = copy(self)
        # This explicit copy must retain the old value after self changes.
        self.value = 19  # tpyc: ok
        return saved.value


def main():
    first = Cell(1)
    second = Cell(2)
    print("singleton", first.singleton(), first.value)
    print("mixed", first.mixed(), first.value)
    print("readonly-distinct", first.inferred(second), second.value)
    print("readonly-shared", first.inferred(first), first.value)
    first.value = 15
    print("optional-distinct", first.optional(second), second.value)
    print("optional-shared", first.optional(first), first.value)
    print("copy", first.owned_copy(), first.value)


main()
