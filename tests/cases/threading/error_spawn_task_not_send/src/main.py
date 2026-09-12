# A task holding a non-Send field (Rc -- non-atomic refcount) cannot satisfy
# Send, so it is rejected at the spawn call site (Send[Own[T]] wrapper) with a
# why-not chain naming the offending field.
from tpy import Own, int32
from tpy.thread import spawn
from tplib.rc import Rc


class BadTask:
    shared: Rc[int32]

    def __init__(self, shared: Own[Rc[int32]]) -> None:
        self.shared = shared

    def run(self) -> int32:
        return self.shared.get()


def main() -> None:
    r = Rc.new(int32(7))
    h = spawn[int32, BadTask](BadTask(r))   # tpyc: error(/'BadTask' is not Send/)
    print(h.join())


main()
