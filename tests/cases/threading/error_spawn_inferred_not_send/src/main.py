# Send enforcement must still fire on the fully-inferred spawn path (no
# explicit type args): a task holding a non-Send field (Rc) is rejected at the
# call site with the why-not chain.
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
    h = spawn(BadTask(r))   # tpyc: error(/'BadTask' is not Send/)
    print(h.join())


main()
