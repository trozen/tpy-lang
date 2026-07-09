# `lock()` auto-derefs through Arc: `arc.lock()` resolves Arc's Deref -> Mutex
# and acquires, no explicit `.get()`. no_cpython because Arc method auto-deref is
# a compile-time deref-chain feature the CPython stubs don't model (see
# tests/cases/tplib/rc_auto_deref, also no_cpython). Single-threaded; the append
# is mutated then observed through a second lock.
from tplib.arc import Arc
from tpy.sync import Mutex


def main() -> None:
    data = Arc.new(Mutex.new([1, 2]))
    with data.lock() as g:          # auto-deref: Arc -> Mutex.lock()
        g.append(3)
    with data.lock() as g:
        print(sorted(g.get()))      # [1, 2, 3]


main()
