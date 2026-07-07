# Arc[T] is Send only when T is Send + Sync. list[Int32] is Send but not Sync,
# so Arc[list[Int32]] is not Send, and spawning a task that owns one is rejected
# at the spawn boundary. The why-not chain reaches the conditional-override
# attribution (the type parameter and the missing Sync marker), not Arc's
# internal control-block pointers.
from tpy import Int32, Own, nocopy
from tplib.arc import Arc
from tpy.thread import spawn


@nocopy
class Task:
    shared: Arc[list[Int32]]

    def __init__(self, shared: Own[Arc[list[Int32]]]) -> None:
        self.shared = shared

    def run(self) -> Int32:
        return len(self.shared.get())


def main() -> None:
    a = Arc.new([1, 2, 3])
    spawn(Task(a.clone()))  # tpyc: error(/not Send/)


main()
