# Forwarding a COPYABLE owned-tuple param while still using it after warns and
# auto-copies into the callee's std::tuple<...>&& param (the `auto(p)` decay-
# copy in the snapshot), mirroring the scalar Own[T] copyable arg. The copy is
# intended (the warning steers to copy() / an owned source at last use); like
# scalar Own copyable, TPy copies here where CPython would alias -- an
# acknowledged, warned divergence. Read-only after the boundary to stay
# CPython-parity-clean; the copy itself is evidenced by the warning + snapshot.
from tpy import Own, int32


class A:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def sink(p: tuple[Own[A], Own[A]]) -> int32:
    return p[0].n + p[1].n


def make() -> tuple[Own[A], Own[A]]:
    return (A(1), A(2))


def fwd(p: tuple[Own[A], Own[A]]) -> int32:
    got = sink(p)  # tpyc: warning(/copies tuple\[Own\[A\], Own\[A\]\] into owned storage/)
    return got + p[0].n


def fwd_local() -> int32:
    t = make()
    got = sink(t)  # tpyc: warning(/copies tuple\[Own\[A\], Own\[A\]\] into owned storage/)
    return got + t[0].n


def main() -> None:
    print(fwd((A(1), A(2))))
    print(fwd_local())


main()
