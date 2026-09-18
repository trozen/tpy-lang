# Capturing a tuple parameter's record element keeps its shared identity;
# scalar members stay snapshots and readonly captures observe other writers.
from __future__ import annotations

from tpy import int32, readonly


class Cell:
    value: int32

    def __init__(self, value: int32):
        self.value = value

    def observe(self, pair: tuple[Cell]) -> int32:
        # Method parameter captures must alias the record reached through the tuple.
        saved = pair[0]  # tpyc: ok
        pair[0].value = 8
        return saved.value


class Observer:
    value: int32

    def __init__(self, pair: tuple[Cell]):
        # Constructor-local captures have the same shared mutation behavior.
        saved = pair[0]  # tpyc: ok
        pair[0].value = 9
        self.value = saved.value


def mixed(pair: tuple[int32, Cell]) -> int32:
    saved = pair[-1]
    # The record aliases the caller's cell, while pair[0] keeps its old scalar value.
    saved.value = 17  # tpyc: ok
    return pair[0]


def singleton(pair: tuple[Cell], writer: Cell) -> int32:
    saved = pair[0]
    # This write must be visible even though saved is never itself mutated.
    writer.value = 21  # tpyc: ok
    return saved.value


def readonly_capture(pair: readonly[tuple[Cell]], writer: Cell) -> int32:
    saved = pair[0]
    # Readonly applies to this access, not to writes through another alias.
    writer.value = 23  # tpyc: ok
    return saved.value


def mixed_access(pair: tuple[readonly[Cell], Cell]) -> int32:
    saved = pair[0]
    # A const tuple wrapper may still contain a mutable record pointer.
    pair[1].value = 25  # tpyc: ok
    return saved.value


def copies(pair: tuple[Cell, int32], writer: Cell) -> int32:
    saved = pair
    # Copying the wrapper retains shared record identity.
    writer.value = 27  # tpyc: ok
    return saved[0].value


def scalar_copies(pair: tuple[int32, bool]) -> int32:
    saved = pair
    current = pair
    # Reseating one tuple local cannot change the earlier copy's scalar value.
    current = (2, False)  # tpyc: ok
    return saved[0]


def main():
    cell = Cell(1)
    other = Cell(2)
    print("mixed", mixed((cell.value, cell)), cell.value)
    print("singleton", singleton((cell,), cell))
    print("readonly", readonly_capture((cell,), cell))
    # Observe the untouched first cell before the shared call changes it to 25.
    print("mixed-access-distinct", mixed_access((cell, other)))
    print("mixed-access-shared", mixed_access((cell, cell)))
    print("tuple-copy-distinct", copies((cell, 1), other))
    print("tuple-copy-shared", copies((cell, 1), cell))
    print("tuple-scalar-copy", scalar_copies((27, True)))
    print("method", cell.observe((cell,)))
    observer = Observer((cell,))
    print("constructor", observer.value, cell.value)


main()
