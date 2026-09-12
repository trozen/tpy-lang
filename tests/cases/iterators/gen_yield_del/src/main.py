# A type with __del__ is non-copyable (codegen deletes the copy ctor), but fine
# under Iterator[T]: the borrow ABI references the element instead of copying it,
# so the deleted copy ctor is never invoked. Exercises the non-@nocopy
# "non-copyable type" path. Mutation through the borrow reaches the source.
from typing import Iterator
from tpy import int32


class Res:
    fd: int32

    def __init__(self, fd: int32) -> None:
        self.fd = fd

    def __del__(self) -> None:
        # Side-effect-free: present only to make Res non-copyable. A printing
        # __del__ would diverge between C++ scope-based and CPython GC timing.
        pass


def gen(items: list[Res]) -> Iterator[Res]:  # tpyc: ok
    for r in items:
        yield r


def main() -> None:
    data = [Res(1), Res(2)]
    for r in gen(data):
        r.fd = r.fd + 10
    for r in data:
        print(r.fd)


main()
