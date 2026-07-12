# tpy: ext_module
# Exposes a TPy class as a real CPython type (PyType_FromSpec): construct from
# Python, call methods, read/write annotated fields as getset descriptors, and
# observe reference semantics -- bump() mutates through a borrowed parameter and
# the change is visible on the SAME object (the headline class-boundary property,
# unlike the by-copy container boundary). make() returns a fresh instance.
from tpy import Int64, Own
from tpy.extern import export


@export
class Counter:
    def __init__(self, value: Int64, label: str):
        self.value = value
        self.label = label

    def incr(self, by: Int64) -> None:
        self.value += by

    def get(self) -> Int64:
        return self.value

    def echo(self, s: str) -> str:
        return s


@export
def bump(c: Counter, by: Int64) -> None:
    c.incr(by)


@export
def make(value: Int64) -> Own[Counter]:
    return Counter(value, "made")
