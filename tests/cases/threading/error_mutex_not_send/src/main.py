# Mutex[T] is Send/Sync only when T is Send. Rc is non-Send (non-atomic
# refcount), so Mutex[Rc[int32]] is non-Send, and a task holding one is rejected
# at the spawn boundary -- the why-not chain walks through the Mutex type
# parameter down to Rc's raw-pointer fields.
from tpy import int32, Own, nocopy
from tplib.rc import Rc
from tpy.thread import spawn
from tpy.sync import Mutex


@nocopy
class Bad:
    shared: Mutex[Rc[int32]]

    def __init__(self, shared: Own[Mutex[Rc[int32]]]) -> None:
        self.shared = shared

    def run(self) -> None:
        pass


def main() -> None:
    spawn(Bad(Mutex.new(Rc.new(int32(1)))))  # tpyc: error(/'Bad' is not Send/)


main()
