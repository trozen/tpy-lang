# The result R crosses the thread boundary (worker -> joiner via std::future),
# so a non-Send R (here Rc, non-atomic refcount) is rejected by the R: Send
# bound on spawn.
from tpy import Own, int32
from tpy.thread import spawn
from tplib.rc import Rc


class MakesRc:
    def run(self) -> Own[Rc[int32]]:
        return Rc.new(int32(5))


def main() -> None:
    h = spawn[Rc[int32], MakesRc](MakesRc())   # tpyc: error(/does not satisfy bound 'Send'/)
    print(h.join().get())


main()
