# Address-of a reference-bound generic param into a Ptr[readonly[W]] slot --
# both arms: a mutable `W` param narrowed to a readonly Ptr, and a
# readonly[W] param to a readonly Ptr (distinct coercion paths).
from typing import Protocol
from tpy import Ptr, readonly, Int32


class Reads(Protocol):
    @readonly
    def value(self) -> Int32: ...


class Counter(Reads):
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    @readonly
    def value(self) -> Int32:
        return self.n


class RWView[W: Reads]:
    # Mutable `W` param -> readonly Ptr: the new arm (UPCAST_TO_CONST_PTR for a
    # bare TypeParamRef actual). Keep the param `W`, not readonly[W] -- the
    # readonly form routes through a different path (see ROView).
    _src: Ptr[readonly[W]]

    def __init__(self, src: W) -> None:
        self._src = src

    def read(self) -> Int32:
        return self._src.value()


class ROView[W: Reads]:
    # readonly[W] param (ReadonlyType(W) actual) -> readonly Ptr: the
    # pre-existing readonly-borrow address-of path.
    _src: Ptr[readonly[W]]

    def __init__(self, src: readonly[W]) -> None:
        self._src = src

    def read(self) -> Int32:
        return self._src.value()


def main() -> None:
    c = Counter(42)
    print(RWView(c).read())   # 42, mutable source -> readonly borrow
    print(ROView(c).read())   # 42, readonly source -> readonly borrow


main()
