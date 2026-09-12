"""Test generic user-defined protocol as type parameter bound.

This tests that protocol type parameters (e.g., T in Container[T]) are
properly substituted when the protocol is used as a bound.
"""
from typing import Protocol
from tpy import int32


class Container[T](Protocol):
    def get(self) -> T: ...
    def set(self, value: T) -> None: ...


class IntBox:
    value: int32

    def __init__(self, v: int32):
        self.value = v

    def get(self) -> int32:
        return self.value

    def set(self, value: int32) -> None:
        self.value = value


class StrBox:
    value: str

    def __init__(self, v: str):
        self.value = v

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


def extract[C: Container[int32]](c: C) -> int32:
    return c.get()


def update[C: Container[int32]](c: C, v: int32) -> None:
    c.set(v)


def extract_str[C: Container[str]](c: C) -> str:
    return c.get()


def main() -> None:
    # Test with IntBox
    box = IntBox(42)
    print(extract(box))

    update(box, 100)
    print(extract(box))

    # Test with StrBox
    sbox = StrBox("hello")
    print(extract_str(sbox))


main()
