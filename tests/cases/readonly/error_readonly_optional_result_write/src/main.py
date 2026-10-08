# A local bound to a declared readonly Optional result is a `const R*`
# alias of the source: writing a field through it is a readonly write.
from typing import Optional

from tpy import int32, readonly


class R:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class H:
    _opt: Optional[R]

    def __init__(self) -> None:
        self._opt = R(1)

    @readonly
    def get(self) -> readonly[Optional[R]]:
        return self._opt


def main() -> None:
    h = H()
    m = h.get()
    if m is not None:
        m.x = 5  # tpyc: error(/readonly/)
    print(m.x if m is not None else -1)


main()
