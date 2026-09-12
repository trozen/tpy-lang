# Fire-and-forget: a task whose run() returns None spawns and joins, in both
# the explicit spawn[None, T] form and the fully-inferred form (R binds the
# void-like return and canonicalizes to None; the generated ThreadTask concept
# accepts the void-returning conformer via proto_result). The task is @nocopy
# so a silent copy at the spawn boundary would be a compile error.
from tpy import int32, nocopy
from tpy.thread import spawn


@nocopy
class Beacon:
    n: int32

    def __init__(self, n: int32):
        self.n = n

    def run(self) -> None:
        pass


def main() -> None:
    h = spawn[None, Beacon](Beacon(1))
    h.join()
    h2 = spawn(Beacon(2))   # tpyc: ok
    h2.join()
    print("joined both")


main()
