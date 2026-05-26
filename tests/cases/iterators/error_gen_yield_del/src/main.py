# Error: a generator yielding a record with __del__ (non-copyable -- codegen
# deletes the copy ctor) is rejected. Exercises the non-@nocopy "non-copyable
# type" branch of the diagnostic (is_type_non_copyable is True but
# is_type_nocopy is False), distinct from the @nocopy branch.
from typing import Iterator
from tpy import Int32


class Res:
    fd: Int32
    def __init__(self, fd: Int32) -> None:
        self.fd = fd
    def __del__(self) -> None:
        print("del")


def gen(items: list[Res]) -> Iterator[Res]:  # tpyc: error(/Generator cannot yield non-copyable type 'Res'/)
    for r in items:
        yield r


def main() -> None:
    pass


main()
