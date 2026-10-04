# A source yielding OWNED elements (`Iterator[Own[P]]`) is not admitted by
# max / min over class instances yet: every step is a fresh object, so the
# result would be a silent value; the form is still refused (TODO.md, "A
# native generic's `-> T` result that IS one of its arguments' elements").
from typing import Iterator
from tpy import Own


class P:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


def key_of(p: P) -> int:
    return p.v


def owns(n: int) -> Iterator[Own[P]]:
    for i in range(n):
        yield P(i)


def main() -> None:
    o = max(owns(3), key=key_of)  # tpyc: error(/No matching overload for max/)
    print(o.v)


main()
