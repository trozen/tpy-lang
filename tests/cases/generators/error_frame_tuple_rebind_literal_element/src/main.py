# A generator's rebound tuple is a pointer tuple, as a plain function body's
# is, so its refusals apply here too: a container literal at an owned element
# has no binding route yet and is refused
# (BUGS.md#ptr-tuple-literal-element-rejects).
from typing import Iterator

from tpy import int32


class A:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def g() -> Iterator[int32]:
    t = ([A(1), A(3)], 2)  # tpyc: error(/element 0 of the tuple bound to 't' here is an expression whose storage is not known here/)
    yield t[0][1].v
    t = ([A(9), A(7)], 8)
    yield t[0][1].v


def main() -> None:
    for v in g():
        print(v)


main()
