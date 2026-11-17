# A generator yielding a view of its by-value parameter: the view outlives
# the frame holding the copy once the consumer keeps it (`list(...)`).
# (docs/LANGUAGE_FEATURES.md: a parameter the function holds its own copy of).
from typing import Iterator
from tpy import ValueType, StrView


class V(ValueType):
    def __init__(self, name: str) -> None:
        self.name = name


def names(x: V) -> Iterator[StrView]:
    yield x.name  # tpyc: error(/StrView referencing parameter 'x' \(the function's own copy\)/)


def main() -> None:
    for s in list(names(V("n" * 30))):
        print(s[:2])


main()
