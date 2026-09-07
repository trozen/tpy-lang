# Storing a record into an `Any`-typed field at the source name's LAST use.
# The payload cannot be read back in-language (no read form of a declared
# `Any` field lowers yet), so the pin is the WRITE render -- which COPIES the
# source instead of moving it (BUGS.md#any-field-write-copies-source).
from typing import Any
from tpy import Int32


class Node:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class Holder:
    payload: Any

    def __init__(self) -> None:
        self.payload = None


def store(h: Holder) -> None:
    n = Node(1)
    h.payload = n  # the last use moves the make_any result, not the source


def main() -> None:
    h = Holder()
    store(h)
    print("stored")


main()
