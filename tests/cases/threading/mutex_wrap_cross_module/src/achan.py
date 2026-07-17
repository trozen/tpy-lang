# A channel-shaped module (Arc[Mutex[Cell]] + Condvar) kept SEPARATE from main.
# Regression guard for the builtin-index ordering bug: tpy.sync's method bodies
# (Mutex.__init__'s `cell.storage` Ptr-deref, MutexGuard.set's UInt32) were
# analyzed against a registry missing Ptr / UInt32 when tpy.sync happened to be
# finalized before tpy._core._containers (where Ptr lives). The order is an
# emergent property of the module graph's name-sorted topo walk -- the module
# name here ('achan') is load-bearing: it places tpy.sync before
# tpy._core._containers, which is exactly the order that used to fail. If a
# future stdlib change shifts that order this case may stop reproducing the
# original bug; the blocking-channel test is the robust real-world guard.
from tpy import UInt32, Own, nocopy, Send
from tpy.mem import UninitHeapStorage
from tpy.sync import Mutex, Condvar
from tplib.arc import Arc


@nocopy
class Cell[T: Send]:
    _buf: UninitHeapStorage[T]
    _cap: UInt32

    def __init__(self, cap: UInt32) -> None:
        self._buf = UninitHeapStorage[T](cap)
        self._cap = cap


@nocopy
class Chan[T: Send]:
    m: Mutex[Cell[T]]
    cv: Condvar

    def __init__(self) -> None:
        self.m = Mutex.new(Cell[T](UInt32(4)))
        self.cv = Condvar()


@nocopy
class Producer[T: Send]:
    c: Arc[Chan[T]]

    def __init__(self, c: Own[Arc[Chan[T]]]) -> None:
        self.c = c

    def capacity(self) -> UInt32:
        chan = self.c.get()
        with chan.m.lock() as g:
            return g.get()._cap


def make_producer[T: Send]() -> Own[Producer[T]]:
    return Producer[T](Arc.new(Chan[T]()))
