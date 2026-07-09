# RwLock[T] carries its own @unsafe_send/@unsafe_sync application (separate from
# Mutex's), so it needs its own rejection guard: RwLock[T] is Send only when T
# is Send. Rc is non-Send (non-atomic refcount), so RwLock[Rc[Int32]] is
# non-Send and a task holding one is rejected at the spawn boundary -- the
# why-not chain walks through the RwLock type parameter down to Rc's fields.
from tpy import Int32, Own, nocopy
from tplib.rc import Rc
from tpy.thread import spawn
from tpy.sync import RwLock


@nocopy
class Bad:
    shared: RwLock[Rc[Int32]]

    def __init__(self, shared: Own[RwLock[Rc[Int32]]]) -> None:
        self.shared = shared

    def run(self) -> None:
        pass


def main() -> None:
    spawn(Bad(RwLock.new(Rc.new(Int32(1)))))  # tpyc: error(/'Bad' is not Send/)


main()
