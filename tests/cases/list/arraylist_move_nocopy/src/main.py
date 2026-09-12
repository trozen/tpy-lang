# Proves ArrayList[T, N] is MOVABLE, not copyable: Handle is @nocopy, so the
# copy path (ArrayList.__copy__ -> copy a Handle) is a compile error. The
# factory return is NRVO/copy-elided, so it does not run the move ctor -- but
# it does prove the move ctor is *selected* over the rejected copy path (the
# build would fail otherwise). The `moved = xs` line then forces a real,
# non-elided move-ctor call (std::move of a named lvalue), so __move__ is also
# exercised at runtime with a @nocopy element.
from tplib import ArrayList
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def make() -> Own[ArrayList[Handle, 4]]:
    xs = ArrayList[Handle, 4]()
    xs.append(Handle(1))
    xs.append(Handle(2))
    return xs  # tpyc: ok


def main() -> None:
    xs = make()
    print(len(xs))            # 2
    print(xs[0].n, xs[1].n)   # 1 2

    moved = xs                # last-use of xs -> forced (non-elided) move ctor
    print(len(moved))         # 2
    moved[1].n = 99
    print(moved[0].n, moved[1].n)   # 1 99


main()
