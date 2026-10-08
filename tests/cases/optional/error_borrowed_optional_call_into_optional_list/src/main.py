# A whole `Optional[R]` local bound from a lending call has no copy arm into a
# `list[Optional[R]]` (BUGS.md#borrowed-pointer-optional-copy-arms): it warns
# and is refused, as its parameter twin is, instead of moving out of the lender.
from typing import Optional
from tpy import int32


class R:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [1, 2, 3]


class H:
    o: Optional[R]

    def __init__(self) -> None:
        self.o = R()

    def geto(self) -> Optional[R]:
        return self.o


def main() -> None:
    h = H()
    ys: list[Optional[R]] = []
    q = h.geto()
    ys.append(q)  # tpyc: warning(/copies R \| None into owned storage/) error(/not yet supported by C\+\+ code generation/)
    print(len(ys))


main()
