# Narrowing a local bound to a declared readonly Optional result keeps it
# readonly: passing it to a parameter the callee mutates is refused.
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


def bump(r: R) -> None:
    r.x += 1


def main() -> None:
    h = H()
    m = h.get()
    if m is not None:
        bump(m)  # tpyc: error(/Cannot pass readonly\[R\] as mutable R/)
    print(m.x if m is not None else -1)


main()
