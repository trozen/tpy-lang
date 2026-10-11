# One C++ pointer cannot hold both a mutable and a read-only object, so the
# re-point rejects (BUGS.md#hoist-const-from-name-wide-borrow-bit).
from typing import Optional

from tpy import int32, readonly


class R:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class H:
    _opt: Optional[R]
    _alt: Optional[R]

    def __init__(self) -> None:
        self._opt = R(1)
        self._alt = R(2)

    @readonly
    def get(self) -> readonly[Optional[R]]:
        return self._opt


def main() -> None:
    h = H()
    r = h._alt
    r = h.get()  # tpyc: error(/decl\.reseat_source/)
    print("reassign", r.x if r is not None else -1)


main()
