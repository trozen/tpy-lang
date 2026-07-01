# A task holding a non-Send field (Rc -- non-atomic refcount) cannot satisfy
# Send, so it is rejected at the spawn call site (Send[Own[T]] wrapper) with a
# why-not chain naming the offending field.
from tpy import Own, Int32
from tpy.thread import spawn
from tplib.rc import Rc


class BadTask:
    shared: Rc[Int32]

    def __init__(self, shared: Own[Rc[Int32]]) -> None:
        self.shared = shared

    def run(self) -> Int32:
        return self.shared.get()


def main() -> None:
    r = Rc.new(Int32(7))
    h = spawn[Int32, BadTask](BadTask(r))   # tpyc: error(/'BadTask' is not Send/)
    print(h.join())


main()
