# Error: a generator stores each yielded value by value and hands out a copy
# from __next__(), so a @nocopy (or otherwise non-copyable) yield type would
# hit a deleted copy ctor in the generated C++ -- rejected up front on both
# codegen paths. A tuple yield (borrow form) would be allowed; a bare nocopy
# record is not.
from typing import Iterator
from tpy import Int32, nocopy


@nocopy
class Handle:
    fd: Int32
    def __init__(self, fd: Int32) -> None:
        self.fd = fd


def handles(items: list[Handle]) -> Iterator[Handle]:  # tpyc: error(/Generator cannot yield @nocopy type 'Handle'/)
    for h in items:
        yield h


def main() -> None:
    pass


main()
